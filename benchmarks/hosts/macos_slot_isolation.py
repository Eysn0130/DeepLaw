"""Credential-free macOS ``sandbox-exec`` launcher for one Host slot.

This module is a development candidate for an owner-controlled launcher.  It
does not provide a broker, a Host runtime, or qualification authority.  The
caller supplies the complete executable argv and the closed read/write and
loopback policy.  A dry-run only builds and hashes that policy; it never counts
as an observed sandbox challenge.

The implementation intentionally has a small public surface:

``MacOSSlotConfig``
    Validates one absolute executable and its explicit policy roots.
``prepare_slot``
    Returns a path-free dry-run summary with configuration/profile digests.
``launch_slot``
    Executes the caller argv through ``/usr/bin/sandbox-exec`` with bounded
    timeout and output accounting.  Raw command, path, stdout and stderr data
    are never returned.

The imported macOS system profile additionally permits system files and IPC;
caller roots are not the complete effective allowlist. This candidate is not
a credential or process-tree isolation boundary. Results remain unqualified.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

SANDBOX_EXECUTABLE = Path("/usr/bin/sandbox-exec")

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_ROOTS = 32
MAX_STAGING_ENTRIES = 4096
MAX_ENDPOINTS = 32
MAX_COMMAND_ARGUMENTS = 128
MAX_COMMAND_ARGUMENT_BYTES = 16 * 1024
MAX_PATH_BYTES = 4096
MAX_PROFILE_BYTES = 256 * 1024
PROCESS_CLEANUP_TIMEOUT_SECONDS = 0.5
OUTPUT_CHUNK_BYTES = 8192

ChallengeKind = Literal["read_denied", "write_denied", "network_denied"]
SlotStatus = Literal[
    "completed",
    "exited",
    "timeout",
    "output_limit",
    "stream_error",
    "cleanup_unconfirmed",
    "staging_integrity_failed",
]

CHALLENGE_MARKERS: dict[ChallengeKind, str] = {
    "read_denied": "DEEPLAW_SLOT_CHALLENGE_READ_DENIED",
    "write_denied": "DEEPLAW_SLOT_CHALLENGE_WRITE_DENIED",
    "network_denied": "DEEPLAW_SLOT_CHALLENGE_NETWORK_DENIED",
}

_NOT_QUALIFIED = (
    "candidate_launcher_only",
    "external_trusted_broker_authority_required",
    "complete_process_tree_observation_unavailable",
    "runtime_path_toctou_unobserved",
    "inherited_system_profile_permissions",
    "formal_qualification_not_executed",
)
_WIDE_ROOTS = frozenset(
    {
        "/",
        "/Applications",
        "/Library",
        "/System",
        "/System/Library",
        "/System/Volumes",
        "/Volumes",
        "/Users",
        "/private",
        "/private/tmp",
        "/private/var",
        "/private/var/folders",
        "/usr",
        "/etc",
        "/dev",
    }
)


class MacOSSlotIsolationError(ValueError):
    """Base error for a malformed or unsafe slot configuration."""


class SlotConfigurationError(MacOSSlotIsolationError):
    """Raised when a caller policy cannot be represented safely."""


class SandboxUnavailableError(RuntimeError):
    """Raised when the required macOS sandbox backend cannot be used."""


class SandboxLaunchError(RuntimeError):
    """Raised when a sandboxed child cannot be started."""


def _configuration_error() -> SlotConfigurationError:
    """Build one path-free configuration error."""

    return SlotConfigurationError("macOS slot configuration is invalid")


def _bounded_values(
    values: Iterable[object],
    *,
    maximum: int,
) -> tuple[object, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise _configuration_error()
    result: list[object] = []
    try:
        for value in values:
            if len(result) >= maximum:
                raise _configuration_error()
            result.append(value)
    except TypeError:
        raise _configuration_error() from None
    return tuple(result)


def _text_value(value: object) -> str:
    if isinstance(value, Path):
        result = str(value)
    elif isinstance(value, str):
        result = value
    else:
        raise _configuration_error()
    if not result or "\x00" in result:
        raise _configuration_error()
    try:
        if len(result.encode("utf-8")) > MAX_COMMAND_ARGUMENT_BYTES:
            raise _configuration_error()
    except UnicodeError:
        raise _configuration_error() from None
    return result


def _sbpl_quote(value: str) -> str:
    """Quote one ASCII path for a Seatbelt profile.

    Control characters and non-ASCII text are rejected instead of relying on
    undocumented SBPL escape behavior.  Backslashes and quotes are escaped so
    a path cannot terminate a literal and append a new rule.
    """

    if not isinstance(value, str) or not value:
        raise _configuration_error()
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise _configuration_error() from None
    if len(encoded) > MAX_PATH_BYTES:
        raise _configuration_error()
    escaped: list[str] = []
    for character in value:
        codepoint = ord(character)
        if codepoint < 0x20 or codepoint == 0x7F or codepoint > 0x7E:
            raise _configuration_error()
        if character == "\\":
            escaped.append("\\\\")
        elif character == '"':
            escaped.append('\\"')
        else:
            escaped.append(character)
    return '"' + "".join(escaped) + '"'


def _is_wide_root(path: Path) -> bool:
    selected = str(path)
    if selected in _WIDE_ROOTS:
        return True
    parts = path.parts
    # A user's home directory is a credential-bearing root.  A project below
    # it can be explicitly selected, but the home itself cannot be a root.
    return len(parts) == 3 and parts[:2] == ("/", "Users")


def _safe_directory(value: object, *, writable: bool) -> Path:
    raw = _text_value(value)
    selected = Path(raw)
    if not selected.is_absolute():
        raise _configuration_error()
    try:
        resolved = selected.resolve(strict=True)
    except (OSError, RuntimeError):
        raise _configuration_error() from None
    # Keep the caller's spelling canonical.  This rejects ``..``, symlinked
    # ancestors, and case aliases on a case-insensitive macOS volume.
    if str(selected) != str(resolved) or selected.is_symlink():
        raise _configuration_error()
    try:
        for ancestor in (selected, *selected.parents):
            metadata = ancestor.stat(follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise _configuration_error()
    except OSError:
        raise _configuration_error() from None
    try:
        metadata = selected.stat(follow_symlinks=False)
    except OSError:
        raise _configuration_error() from None
    if not stat.S_ISDIR(metadata.st_mode) or _is_wide_root(resolved):
        raise _configuration_error()
    # A writable policy root must be owner-controlled.  Read roots may be
    # immutable system directories, but neither kind may be group/world-writable.
    if metadata.st_mode & 0o022:
        raise _configuration_error()
    if writable and metadata.st_uid != os.geteuid():
        raise _configuration_error()
    _sbpl_quote(str(resolved))
    return resolved


def _validate_staging_trees(config: MacOSSlotConfig) -> None:
    """Reject pre-existing inode aliases; concurrent path swaps remain unqualified.

    The two immutable system executable directories are already part of the
    runtime policy. All caller data roots must be dedicated, bounded trees.
    This preflight is not an OS-enforced protection against another writer.
    """

    count = 0
    for root in (*config.allowed_read_roots, *config.allowed_write_roots):
        writable = root in config.allowed_write_roots
        _safe_directory(root, writable=writable)
        if not writable and root in (Path("/bin"), Path("/usr/bin")):
            continue
        pending = [root]
        while pending:
            directory = pending.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        count += 1
                        if count > MAX_STAGING_ENTRIES:
                            raise _configuration_error()
                        metadata = entry.stat(follow_symlinks=False)
                        if stat.S_ISDIR(metadata.st_mode):
                            pending.append(Path(entry.path))
                        elif not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                            raise _configuration_error()
                        if metadata.st_mode & 0o022:
                            raise _configuration_error()
            except OSError:
                raise _configuration_error() from None


def _safe_file(value: object) -> Path:
    raw = _text_value(value)
    selected = Path(raw)
    if not selected.is_absolute():
        raise _configuration_error()
    try:
        resolved = selected.resolve(strict=True)
    except (OSError, RuntimeError):
        raise _configuration_error() from None
    if str(selected) != str(resolved) or selected.is_symlink():
        raise _configuration_error()
    try:
        for ancestor in (selected, *selected.parents):
            metadata = ancestor.stat(follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                raise _configuration_error()
        metadata = selected.stat(follow_symlinks=False)
    except OSError:
        raise _configuration_error() from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or not metadata.st_mode & 0o111
        or metadata.st_nlink != 1
    ):
        raise _configuration_error()
    _sbpl_quote(str(resolved))
    return resolved


def _is_under(root: Path, selected: Path) -> bool:
    return selected == root or root in selected.parents


def _reject_nested_roots(roots: Sequence[Path]) -> None:
    if len(set(roots)) != len(roots):
        raise _configuration_error()
    for index, root in enumerate(roots):
        if any(
            index != other_index
            and (_is_under(root, other) or _is_under(other, root))
            for other_index, other in enumerate(roots)
        ):
            raise _configuration_error()


def _normalise_endpoint(value: object) -> LoopbackEndpoint:
    if isinstance(value, LoopbackEndpoint):
        host, port = value.host, value.port
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) != 2:
            raise _configuration_error()
        host, port = value[0], value[1]
    else:
        raise _configuration_error()
    if not isinstance(host, str) or not host:
        raise _configuration_error()
    # SBPL accepts only localhost or wildcard hosts here. Localhost permits
    # both IPv4 and IPv6 loopback; do not advertise an IPv4-only capability.
    if host != "localhost":
        raise _configuration_error()
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise _configuration_error()
    return LoopbackEndpoint(host="localhost", port=port)


@dataclass(frozen=True, slots=True)
class LoopbackEndpoint:
    """One outbound TCP port on both IPv4 and IPv6 loopback."""

    host: str
    port: int


@dataclass(frozen=True, slots=True, init=False)
class MacOSSlotConfig:
    """Validated configuration for one caller-supplied macOS slot command."""

    command: tuple[str, ...]
    allowed_read_roots: tuple[Path, ...]
    allowed_write_roots: tuple[Path, ...]
    allowed_loopback_endpoints: tuple[LoopbackEndpoint, ...]
    cwd: Path
    timeout_seconds: float
    max_output_bytes: int
    _config_sha256: str = field(repr=False)

    def __init__(
        self,
        command: Sequence[str | Path],
        allowed_read_roots: Iterable[str | Path] | None = None,
        allowed_write_roots: Iterable[str | Path] | None = None,
        allowed_loopback_endpoints: Iterable[LoopbackEndpoint | Sequence[object]] | None = None,
        *,
        cwd: str | Path | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        read_roots: Iterable[str | Path] | None = None,
        write_roots: Iterable[str | Path] | None = None,
        loopback_endpoints: Iterable[LoopbackEndpoint | Sequence[object]] | None = None,
    ) -> None:
        if allowed_read_roots is not None and read_roots is not None:
            raise _configuration_error()
        if allowed_write_roots is not None and write_roots is not None:
            raise _configuration_error()
        if allowed_loopback_endpoints is not None and loopback_endpoints is not None:
            raise _configuration_error()
        read_values = (
            allowed_read_roots if allowed_read_roots is not None else read_roots or ()
        )
        write_values = (
            allowed_write_roots if allowed_write_roots is not None else write_roots or ()
        )
        endpoint_values = (
            allowed_loopback_endpoints
            if allowed_loopback_endpoints is not None
            else loopback_endpoints or ()
        )
        command_values = _bounded_values(command, maximum=MAX_COMMAND_ARGUMENTS)
        if not command_values:
            raise _configuration_error()
        command_text = tuple(_text_value(value) for value in command_values)
        executable = _safe_file(command_text[0])
        read_candidates = _bounded_values(read_values, maximum=MAX_ROOTS)
        write_candidates = _bounded_values(write_values, maximum=MAX_ROOTS)
        if not read_candidates:
            raise _configuration_error()
        reads = tuple(_safe_directory(value, writable=False) for value in read_candidates)
        writes = tuple(_safe_directory(value, writable=True) for value in write_candidates)
        _reject_nested_roots((*reads, *writes))
        if not any(_is_under(root, executable) for root in reads):
            raise _configuration_error()
        endpoint_candidates = _bounded_values(endpoint_values, maximum=MAX_ENDPOINTS)
        endpoints = tuple(_normalise_endpoint(value) for value in endpoint_candidates)
        if len(set(endpoints)) != len(endpoints):
            raise _configuration_error()
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise _configuration_error()
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
            raise _configuration_error()
        if (
            isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or not 1 <= max_output_bytes <= MAX_OUTPUT_BYTES
        ):
            raise _configuration_error()
        if cwd is None:
            current_directory = writes[0] if writes else reads[0]
        else:
            current_directory = _safe_directory(cwd, writable=False)
            if not any(
                _is_under(root, current_directory) for root in (*reads, *writes)
            ):
                raise _configuration_error()
        payload = {
            "schema_version": "deeplaw.macos-slot-config/v1",
            "command": list(command_text),
            "allowed_read_roots": [str(root) for root in reads],
            "allowed_write_roots": [str(root) for root in writes],
            "allowed_loopback_endpoints": [
                {"host": endpoint.host, "port": endpoint.port} for endpoint in endpoints
            ],
            "cwd": str(current_directory),
            "timeout_seconds": timeout,
            "max_output_bytes": max_output_bytes,
        }
        digest = _sha256(_canonical_json(payload).encode("utf-8"))
        object.__setattr__(self, "command", command_text)
        object.__setattr__(self, "allowed_read_roots", reads)
        object.__setattr__(self, "allowed_write_roots", writes)
        object.__setattr__(self, "allowed_loopback_endpoints", endpoints)
        object.__setattr__(self, "cwd", current_directory)
        object.__setattr__(self, "timeout_seconds", timeout)
        object.__setattr__(self, "max_output_bytes", max_output_bytes)
        object.__setattr__(self, "_config_sha256", digest)
        _validate_staging_trees(self)

    @property
    def config_sha256(self) -> str:
        """Digest of the complete normalized policy, including command argv."""

        return self._config_sha256

    @property
    def read_roots(self) -> tuple[Path, ...]:
        """Compatibility alias for the explicit read policy."""

        return self.allowed_read_roots

    @property
    def write_roots(self) -> tuple[Path, ...]:
        """Compatibility alias for the explicit write policy."""

        return self.allowed_write_roots

    @property
    def loopback_endpoints(self) -> tuple[LoopbackEndpoint, ...]:
        """Compatibility alias for the explicit network policy."""

        return self.allowed_loopback_endpoints

    def __repr__(self) -> str:
        return (
            "MacOSSlotConfig("
            f"config_sha256={self.config_sha256!r}, "
            f"read_root_count={len(self.allowed_read_roots)}, "
            f"write_root_count={len(self.allowed_write_roots)}, "
            f"loopback_endpoint_count={len(self.allowed_loopback_endpoints)})"
        )


@dataclass(frozen=True, slots=True)
class SlotPlan:
    """Path-free dry-run result; no sandbox challenge was observed."""

    config_sha256: str
    profile_sha256: str
    sandbox_available: bool
    marker_observed: bool = False
    process_tree_observed: bool = False
    process_tree_cleanup_observed: bool = False
    formal_qualification: bool = False
    not_qualified: tuple[str, ...] = _NOT_QUALIFIED

    def to_public_dict(self) -> dict[str, object]:
        return {
            "schema_version": "deeplaw.macos-slot-plan/v1",
            "config_sha256": self.config_sha256,
            "profile_sha256": self.profile_sha256,
            "sandbox_available": self.sandbox_available,
            "marker_observed": self.marker_observed,
            "process_tree_observed": self.process_tree_observed,
            "process_tree_cleanup_observed": self.process_tree_cleanup_observed,
            "formal_qualification": self.formal_qualification,
            "not_qualified": list(self.not_qualified),
        }


@dataclass(frozen=True, slots=True)
class SlotLaunchResult:
    """Bounded result: backend spawn and markers do not attest profile acceptance."""

    status: SlotStatus
    returncode: int | None
    timed_out: bool
    output_limit_exceeded: bool
    stdout_bytes: int
    stderr_bytes: int
    config_sha256: str
    profile_sha256: str
    sandbox_exec_spawned: bool
    marker_observed: bool
    process_tree_observed: bool = False
    process_tree_cleanup_observed: bool = False
    formal_qualification: bool = False
    not_qualified: tuple[str, ...] = _NOT_QUALIFIED

    def to_public_dict(self) -> dict[str, object]:
        """Return the only representation suitable for logs or receipts."""

        return {
            "schema_version": "deeplaw.macos-slot-result/v1",
            "status": self.status,
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "output_limit_exceeded": self.output_limit_exceeded,
            "stdout_bytes": self.stdout_bytes,
            "stderr_bytes": self.stderr_bytes,
            "config_sha256": self.config_sha256,
            "profile_sha256": self.profile_sha256,
            "sandbox_exec_spawned": self.sandbox_exec_spawned,
            "marker_observed": self.marker_observed,
            "process_tree_observed": self.process_tree_observed,
            "process_tree_cleanup_observed": self.process_tree_cleanup_observed,
            "formal_qualification": self.formal_qualification,
            "not_qualified": list(self.not_qualified),
        }


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sandbox_backend_available() -> bool:
    """Return whether the fixed macOS backend is a regular executable file."""

    if sys.platform != "darwin":
        return False
    try:
        metadata = SANDBOX_EXECUTABLE.stat(follow_symlinks=False)
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and not SANDBOX_EXECUTABLE.is_symlink()
        and bool(metadata.st_mode & 0o111)
    )


def build_sandbox_profile(config: MacOSSlotConfig) -> str:
    """Render one closed SBPL profile for ``config``.

    ``system.sb`` additionally grants system file reads and IPC. Its effective
    permissions are not restricted to loader resources or caller roots. The
    explicit network rules below deny imported network permissions and admit
    only outbound TCP on the configured dual-stack loopback ports.
    """

    if not isinstance(config, MacOSSlotConfig):
        raise _configuration_error()
    lines = [
        "(version 1)",
        "(deny default)",
        '(import "system.sb")',
        "(deny network-inbound network-outbound)",
        "(deny dynamic-code-generation)",
        "(allow process*)",
    ]
    read_paths = " ".join(
        f"(subpath {_sbpl_quote(str(root))})" for root in config.allowed_read_roots
    )
    lines.append(f"(allow file-read* {read_paths})")
    if config.allowed_write_roots:
        write_paths = " ".join(
            f"(subpath {_sbpl_quote(str(root))})" for root in config.allowed_write_roots
        )
        lines.append(f"(allow file-write* {write_paths})")
    for endpoint in config.allowed_loopback_endpoints:
        port = f"localhost:{endpoint.port}"
        lines.append(
            f'(allow network-outbound (remote tcp {_sbpl_quote(port)}))'
        )
    profile = "\n".join(lines) + "\n"
    if len(profile.encode("utf-8")) > MAX_PROFILE_BYTES:
        raise _configuration_error()
    return profile


def prepare_slot(config: MacOSSlotConfig) -> SlotPlan:
    """Build a dry-run plan without starting ``sandbox-exec``."""

    profile = build_sandbox_profile(config)
    available = sandbox_backend_available()
    extra = () if available else ("sandbox_backend_unavailable",)
    return SlotPlan(
        config_sha256=config.config_sha256,
        profile_sha256=_sha256(profile.encode("utf-8")),
        sandbox_available=available,
        not_qualified=(*_NOT_QUALIFIED, *extra),
    )


def challenge_marker(kind: ChallengeKind) -> str:
    """Return a fixed marker that a synthetic challenge may print."""

    if kind not in CHALLENGE_MARKERS:
        raise _configuration_error()
    return CHALLENGE_MARKERS[kind]


class _MarkerScanner:
    def __init__(self, markers: Sequence[str]) -> None:
        self._markers = {marker.encode("ascii") for marker in markers}
        self._tail = b""
        self.seen: set[str] = set()
        self._maximum_marker_bytes = max((len(marker) for marker in self._markers), default=0)
        self._lock = threading.Lock()

    def feed(self, chunk: bytes) -> None:
        if not self._markers:
            return
        with self._lock:
            data = self._tail + chunk
            for marker in self._markers:
                if marker in data:
                    self.seen.add(marker.decode("ascii"))
            self._tail = data[-max(self._maximum_marker_bytes - 1, 0) :]


@dataclass
class _StreamState:
    count: int = 0
    output_limit: bool = False
    error: bool = False


@dataclass
class _OutputBudget:
    maximum: int
    total: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reserve(self, size: int) -> tuple[int, bool]:
        with self.lock:
            if self.total >= self.maximum:
                return 0, True
            accepted = min(size, self.maximum - self.total)
            self.total += accepted
            return accepted, accepted != size


def _drain_stream(
    stream: object,
    *,
    budget: _OutputBudget,
    scanner: _MarkerScanner,
    state: _StreamState,
    output_event: threading.Event,
) -> None:
    reader = stream
    try:
        while True:
            chunk = reader.read(OUTPUT_CHUNK_BYTES)
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                state.error = True
                break
            accepted, exceeded = budget.reserve(len(chunk))
            state.count += accepted
            scanner.feed(chunk[:accepted])
            if exceeded:
                state.output_limit = True
                output_event.set()
                break
    except (OSError, ValueError):
        state.error = True


def _terminate_process_group(process: subprocess.Popen[bytes]) -> bool:
    """Return group-signal/leader-reap success, never whole-tree containment."""

    group_signal_ok = True
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        group_signal_ok = False
        with suppress(OSError):
            process.terminate()
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=PROCESS_CLEANUP_TIMEOUT_SECONDS)
    # A reaped leader does not imply that its children exited on SIGTERM.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        group_signal_ok = False
        with suppress(OSError):
            process.kill()
    try:
        process.wait(timeout=PROCESS_CLEANUP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return False
    return group_signal_ok


def _minimal_environment(config: MacOSSlotConfig) -> dict[str, str]:
    root = config.allowed_write_roots[0] if config.allowed_write_roots else config.cwd
    # This dictionary is intentionally constructed from constants and explicit
    # config roots.  It never copies os.environ and has no credential opt-in.
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "LC_CTYPE": "C",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "HOME": str(root),
        "TMPDIR": str(root),
    }


def _validate_expected_challenges(
    expected_challenges: Iterable[ChallengeKind],
) -> tuple[ChallengeKind, ...]:
    values = _bounded_values(expected_challenges, maximum=len(CHALLENGE_MARKERS))
    result: list[ChallengeKind] = []
    for value in values:
        if value not in CHALLENGE_MARKERS:
            raise _configuration_error()
        typed = value
        if typed in result:
            raise _configuration_error()
        result.append(typed)
    return tuple(result)


def launch_slot(
    config: MacOSSlotConfig,
    *,
    expected_challenges: Iterable[ChallengeKind] = (),
) -> SlotLaunchResult:
    """Run one argv through the fixed sandbox backend.

    ``expected_challenges`` is only a bounded marker observation aid for
    synthetic tests.  A marker is not an external attestation and never makes
    ``formal_qualification`` true.
    """

    if not isinstance(config, MacOSSlotConfig):
        raise _configuration_error()
    _validate_staging_trees(config)
    _safe_file(config.command[0])
    expected = _validate_expected_challenges(expected_challenges)
    if not sandbox_backend_available():
        raise SandboxUnavailableError("macOS sandbox backend is unavailable")
    profile = build_sandbox_profile(config)
    profile_sha256 = _sha256(profile.encode("utf-8"))
    marker_scanner = _MarkerScanner([CHALLENGE_MARKERS[kind] for kind in expected])
    output_event = threading.Event()
    output_budget = _OutputBudget(config.max_output_bytes)
    stdout_state = _StreamState()
    stderr_state = _StreamState()
    process: subprocess.Popen[bytes] | None = None
    try:
        try:
            process = subprocess.Popen(
                [
                    str(SANDBOX_EXECUTABLE),
                    "-p",
                    profile,
                    *config.command,
                ],
                cwd=str(config.cwd),
                env=_minimal_environment(config),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                close_fds=True,
                shell=False,
                start_new_session=True,
            )
        except OSError:
            raise SandboxLaunchError("sandboxed slot launch failed") from None
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_thread = threading.Thread(
            target=_drain_stream,
            args=(process.stdout,),
            kwargs={
                "budget": output_budget,
                "scanner": marker_scanner,
                "state": stdout_state,
                "output_event": output_event,
            },
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_drain_stream,
            args=(process.stderr,),
            kwargs={
                "budget": output_budget,
                "scanner": marker_scanner,
                "state": stderr_state,
                "output_event": output_event,
            },
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        deadline = time.monotonic() + config.timeout_seconds
        timed_out = False
        output_limit = False
        while process.poll() is None:
            if output_event.is_set():
                output_limit = True
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                process.wait(timeout=min(0.05, remaining))
            except subprocess.TimeoutExpired:
                continue
        cleanup_ok = True
        if timed_out or output_limit:
            cleanup_ok = _terminate_process_group(process)
        returncode = process.poll()
        if returncode is None:
            cleanup_ok = _terminate_process_group(process) and cleanup_ok
            returncode = process.poll()
        stdout_thread.join(timeout=PROCESS_CLEANUP_TIMEOUT_SECONDS)
        stderr_thread.join(timeout=PROCESS_CLEANUP_TIMEOUT_SECONDS)
        status: SlotStatus
        if not cleanup_ok or stdout_thread.is_alive() or stderr_thread.is_alive():
            status = "cleanup_unconfirmed"
        elif timed_out:
            status = "timeout"
        elif output_limit or stdout_state.output_limit or stderr_state.output_limit:
            status = "output_limit"
        elif stdout_state.error or stderr_state.error:
            status = "stream_error"
        elif returncode == 0:
            status = "completed"
        else:
            status = "exited"
        try:
            _validate_staging_trees(config)
        except SlotConfigurationError:
            status = "staging_integrity_failed"
        observed = bool(expected) and all(
            CHALLENGE_MARKERS[kind] in marker_scanner.seen for kind in expected
        )
        not_qualified = _NOT_QUALIFIED
        if expected and not observed:
            not_qualified = (*not_qualified, "requested_challenge_not_observed")
        return SlotLaunchResult(
            status=status,
            returncode=returncode,
            timed_out=timed_out,
            output_limit_exceeded=(
                output_limit or stdout_state.output_limit or stderr_state.output_limit
            ),
            stdout_bytes=min(stdout_state.count, config.max_output_bytes),
            stderr_bytes=min(stderr_state.count, config.max_output_bytes),
            config_sha256=config.config_sha256,
            profile_sha256=profile_sha256,
            sandbox_exec_spawned=True,
            marker_observed=observed,
            not_qualified=not_qualified,
        )
    finally:
        if process is not None:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


# Explicit aliases make the small seam easy to discover without introducing a
# second implementation or a compatibility launcher.
build_profile = build_sandbox_profile
dry_run_slot = prepare_slot
launch_sandboxed_slot = launch_slot
launch_macos_slot = launch_slot


__all__ = [
    "CHALLENGE_MARKERS",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_OUTPUT_BYTES",
    "MAX_TIMEOUT_SECONDS",
    "LoopbackEndpoint",
    "MacOSSlotConfig",
    "MacOSSlotIsolationError",
    "SandboxLaunchError",
    "SandboxUnavailableError",
    "SlotConfigurationError",
    "SlotLaunchResult",
    "SlotPlan",
    "build_profile",
    "build_sandbox_profile",
    "challenge_marker",
    "dry_run_slot",
    "launch_macos_slot",
    "launch_sandboxed_slot",
    "launch_slot",
    "prepare_slot",
    "sandbox_backend_available",
]
