"""Bounded, path-free process-tree metadata for an owner-side Linux observer.

The Linux ``cn_proc`` decoder is deliberately a separate component.  This
module consumes its :class:`ProcessEvent` values after an external producer has
bound the stream to the kernel source and closed the per-CPU sequence window.
It reconstructs only the role process tree and lifecycle facts needed by an
owner review.  It does not subscribe to ``cn_proc``, inspect ``/proc``, read a
command or environment, or make a qualification/admission decision.

``source_bound`` and ``cpu_windows_closed`` are owner-supplied attestations of
those external checks.  They are accepted only as strict booleans and are
never inferred from the event list.  A receipt is therefore non-formal even
when the tree is complete; an external authority must perform final admission.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import islice
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:  # pragma: no cover - imported only for static type checkers
    from .linux_proc_connector import ProcessEvent


SCHEMA_VERSION: Final = "deeplaw.linux-process-tree-metadata/v1"
ROLES: Final = ("host", "mcp")
MAX_EVENTS: Final = 8192
MAX_NODES: Final = 4096
MAX_PID: Final = 2**31 - 1
MAX_UID: Final = 2**32 - 1
MAX_SEQUENCE: Final = 2**32 - 1
MAX_TIMESTAMP_NS: Final = 2**63 - 1
MAX_CPU: Final = 2**32 - 1
MAX_EVENT_VALUE: Final = 2**32 - 1

_WHAT_BY_NUMBER: Final = {
    0x00000000: "NONE",
    0x00000001: "FORK",
    0x00000002: "EXEC",
    0x00000004: "UID",
    0x00000040: "GID",
    0x00000080: "SID",
    0x00000100: "PTRACE",
    0x00000200: "COMM",
    0x40000000: "COREDUMP",
    0x80000000: "EXIT",
}
_WHAT_ALIASES: Final = {
    "PROCESS_FORK": "FORK",
    "PROCESS_EXEC": "EXEC",
    "EXECVE": "EXEC",
    "PROCESS_EXIT": "EXIT",
}
_LIFECYCLE_WHATS: Final = frozenset({"FORK", "EXEC", "EXIT"})
_PID_DIGEST_PREFIX: Final = b"deeplaw.linux-process-tree-metadata/v1:pid:"


class ProcessTreeMetadataError(ValueError):
    """A malformed or otherwise unsafe metadata receipt/input."""


class ProcessTreeMetadataGap(ProcessTreeMetadataError):
    """A typed observation gap that cannot support a complete tree."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def canonical_json(value: Any) -> bytes:
    """Encode metadata deterministically without JSON extensions."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as error:
        raise ProcessTreeMetadataError("metadata is not canonical JSON") from error


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def pid_sha256(pid: int) -> str:
    """Return a public, domain-separated digest for one numeric PID."""

    if isinstance(pid, bool) or not isinstance(pid, int) or not 1 <= pid <= MAX_PID:
        raise ProcessTreeMetadataError("pid is invalid")
    return sha256_hex(_PID_DIGEST_PREFIX + str(pid).encode("ascii"))


@dataclass(frozen=True, slots=True)
class RoleSeed:
    """Owner-frozen identity captured while one role PID was alive.

    ``pid`` and ``captured_at_ns`` remain owner-internal and are intentionally
    absent from :meth:`to_public`.
    """

    role: str
    pid: int
    uid: int
    captured_at_ns: int

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ProcessTreeMetadataGap("role_seed_invalid")
        if (
            isinstance(self.pid, bool)
            or not isinstance(self.pid, int)
            or not 1 <= self.pid <= MAX_PID
        ):
            raise ProcessTreeMetadataGap("role_seed_invalid")
        if (
            isinstance(self.uid, bool)
            or not isinstance(self.uid, int)
            or not 0 <= self.uid <= MAX_UID
        ):
            raise ProcessTreeMetadataGap("role_seed_invalid")
        if (
            isinstance(self.captured_at_ns, bool)
            or not isinstance(self.captured_at_ns, int)
            or not 0 <= self.captured_at_ns <= MAX_TIMESTAMP_NS
        ):
            raise ProcessTreeMetadataGap("role_seed_invalid")

    @property
    def pid_digest(self) -> str:
        return pid_sha256(self.pid)

    def to_public(self) -> dict[str, Any]:
        return {"role": self.role, "pid_sha256": self.pid_digest}


def normalize_role_seeds(
    roots: Mapping[str, RoleSeed | Mapping[str, Any]],
) -> tuple[RoleSeed, ...]:
    """Validate the exact owner root shape without exposing root values."""

    if not isinstance(roots, Mapping) or set(roots) != set(ROLES):
        raise ProcessTreeMetadataGap("role_seed_invalid")
    normalized: list[RoleSeed] = []
    seen_pids: set[int] = set()
    for role in ROLES:
        value = roots[role]
        if isinstance(value, RoleSeed):
            seed = value
            if seed.role != role:
                raise ProcessTreeMetadataGap("role_seed_invalid")
        elif isinstance(value, Mapping) and set(value) == {"pid", "uid", "captured_at_ns"}:
            seed = RoleSeed(
                role=role,
                pid=value["pid"],
                uid=value["uid"],
                captured_at_ns=value["captured_at_ns"],
            )
        else:
            raise ProcessTreeMetadataGap("role_seed_invalid")
        if seed.pid in seen_pids:
            raise ProcessTreeMetadataGap("role_seed_collision")
        seen_pids.add(seed.pid)
        normalized.append(seed)
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class _Event:
    what: str
    cpu: int
    sequence: int
    timestamp_ns: int
    values: Mapping[str, int] = field(repr=False)
    ordinal: int = field(repr=False)

    def digest_projection(self) -> dict[str, Any]:
        # Raw numeric IDs are used only inside the owner-side digest.  No event
        # record is returned to the provider-facing projection.
        return {
            "what": self.what,
            "cpu": self.cpu,
            "sequence": self.sequence,
            "timestamp_ns": self.timestamp_ns,
            "values": dict(sorted(self.values.items())),
        }


@dataclass(slots=True)
class _Node:
    role: str
    pid: int
    tgid: int
    generation: int
    parent_pid: int
    parent_tgid: int
    root: bool
    birth_ordinal: int
    exec_count: int = 0
    exit_count: int = 0
    exit_code: int | None = None


def _member(event: Any, name: str) -> Any:
    missing = object()
    if isinstance(event, Mapping):
        value = event.get(name, missing)
    else:
        value = getattr(event, name, missing)
    if value is missing:
        raise ProcessTreeMetadataGap("event_invalid")
    return value


def _normalize_what(value: Any) -> str:
    if isinstance(value, bool):
        raise ProcessTreeMetadataGap("event_type_unknown")
    name = getattr(value, "name", None)
    if isinstance(name, str):
        token = name.upper()
    elif isinstance(value, str):
        token = value.upper().replace("-", "_")
    elif isinstance(value, int):
        try:
            token = _WHAT_BY_NUMBER[value]
        except KeyError as error:
            raise ProcessTreeMetadataGap("event_type_unknown") from error
    else:
        raise ProcessTreeMetadataGap("event_type_unknown")
    token = _WHAT_ALIASES.get(token, token)
    if token not in _WHAT_BY_NUMBER.values():
        raise ProcessTreeMetadataGap("event_type_unknown")
    return token


def _parse_event(event: Any, ordinal: int) -> _Event:
    if not 1 <= ordinal <= MAX_EVENTS:
        raise ProcessTreeMetadataGap("event_count_exceeded")
    what = _normalize_what(_member(event, "what"))
    cpu = _member(event, "cpu")
    sequence = _member(event, "sequence")
    timestamp_ns = _member(event, "timestamp_ns")
    for name, value, minimum, maximum in (
        ("cpu", cpu, 0, MAX_CPU),
        ("sequence", sequence, 0, MAX_SEQUENCE),
        ("timestamp_ns", timestamp_ns, 0, MAX_TIMESTAMP_NS),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            raise ProcessTreeMetadataGap(f"{name}_invalid")
    raw_values = _member(event, "values")
    if not isinstance(raw_values, Mapping) or any(
        not isinstance(key, str) or key.lower() == "comm" for key in raw_values
    ):
        raise ProcessTreeMetadataGap("event_values_invalid")
    values: dict[str, int] = {}
    for key, value in raw_values.items():
        if (
            not isinstance(key, str)
            or not key
            or isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= MAX_EVENT_VALUE
        ):
            raise ProcessTreeMetadataGap("event_values_invalid")
        values[key] = value
    return _Event(
        what=what,
        cpu=cpu,
        sequence=sequence,
        timestamp_ns=timestamp_ns,
        values=values,
        ordinal=ordinal,
    )


def _required_int(
    event: _Event,
    name: str,
    *,
    minimum: int = 1,
    maximum: int = MAX_PID,
) -> int:
    try:
        value = event.values[name]
    except KeyError as error:
        raise ProcessTreeMetadataGap("event_values_invalid") from error
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ProcessTreeMetadataGap("event_values_invalid")
    return value


def _fork_values(event: _Event) -> tuple[int, int, int, int]:
    if event.what != "FORK":
        raise ProcessTreeMetadataError("not a fork event")
    return (
        _required_int(event, "parent_pid"),
        _required_int(event, "parent_tgid"),
        _required_int(event, "child_pid"),
        _required_int(event, "child_tgid"),
    )


def _process_values(event: _Event) -> tuple[int, int]:
    if event.what not in {"EXEC", "EXIT"}:
        raise ProcessTreeMetadataError("not a process lifecycle event")
    return (
        _required_int(event, "process_pid"),
        _required_int(event, "process_tgid"),
    )


def _exit_values(event: _Event) -> tuple[int, int, int]:
    pid, tgid = _process_values(event)
    exit_code = _required_int(event, "exit_code", minimum=0, maximum=MAX_EVENT_VALUE)
    return pid, tgid, exit_code


def _event_order(event: _Event) -> tuple[int, int, int, int]:
    return event.timestamp_ns, event.sequence, event.cpu, event.ordinal


def _record_digest(events: list[_Event]) -> str:
    return sha256_hex(
        canonical_json(
            [event.digest_projection() for event in sorted(events, key=_event_order)]
        )
    )


def _graph_projection(nodes: list[_Node]) -> list[dict[str, Any]]:
    projection: list[dict[str, Any]] = []
    for node in sorted(
        nodes,
        key=lambda item: (item.role, pid_sha256(item.pid), item.generation),
    ):
        projection.append(
            {
                "role": node.role,
                "pid_sha256": pid_sha256(node.pid),
                "tgid_sha256": pid_sha256(node.tgid),
                "parent_pid_sha256": pid_sha256(node.parent_pid),
                "parent_tgid_sha256": pid_sha256(node.parent_tgid),
                "generation": node.generation,
                "root": node.root,
                "exec_count": node.exec_count,
                "exit_count": node.exit_count,
                "exit_code": node.exit_code,
            }
        )
    return projection


def _graph_digest(nodes: list[_Node]) -> str:
    return sha256_hex(canonical_json(_graph_projection(nodes)))


def _role_projection(seed: RoleSeed, nodes: list[_Node]) -> dict[str, Any]:
    selected = [item for item in nodes if item.role == seed.role]
    return {
        "role": seed.role,
        "root_pid_sha256": seed.pid_digest,
        "pid_sha256": [
            pid_sha256(item.pid)
            for item in sorted(
                selected, key=lambda item: (pid_sha256(item.pid), item.generation)
            )
        ],
        "counts": {
            "nodes": len(selected),
            "forks": max(0, len(selected) - 1),
            "execs": sum(item.exec_count for item in selected),
            "exits": sum(item.exit_count for item in selected),
        },
    }


def _receipt_digest(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    return sha256_hex(canonical_json(body))


def _receipt(
    *,
    status: str,
    roots: tuple[RoleSeed, ...],
    events: list[_Event],
    nodes: list[_Node],
    failures: list[str],
) -> dict[str, Any]:
    counts = {
        "events": len(events),
        "nodes": len(nodes),
        "forks": sum(event.what == "FORK" for event in events),
        "execs": sum(event.what == "EXEC" for event in events),
        "exits": sum(event.what == "EXIT" for event in events),
    }
    value: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "formal_admission": False,
        "claim_eligible": False,
        "roles": [_role_projection(seed, nodes) for seed in roots],
        "counts": counts,
        "graph_sha256": _graph_digest(nodes),
        "event_sequence_sha256": _record_digest(events),
        "failure_codes": sorted(set(failures)),
        "record_sha256": "",
    }
    value["record_sha256"] = _receipt_digest(value)
    return value


def _check_external_gates(
    source_bound: bool | None,
    cpu_windows_closed: bool | None,
) -> list[str]:
    failures: list[str] = []
    if source_bound is None:
        failures.append("source_binding_missing")
    elif type(source_bound) is not bool:
        failures.append("source_binding_invalid")
    elif not source_bound:
        failures.append("source_unbound")
    if cpu_windows_closed is None:
        failures.append("cpu_window_evidence_missing")
    elif type(cpu_windows_closed) is not bool:
        failures.append("cpu_window_evidence_invalid")
    elif not cpu_windows_closed:
        failures.append("cpu_window_unclosed")
    return failures


def _check_pid_births(events: list[_Event]) -> tuple[dict[int, int], list[str]]:
    """Detect active PID double-births and retain a per-PID generation count."""

    active: dict[int, int] = {}
    generations: dict[int, int] = {}
    births_by_ordinal: dict[int, int] = {}
    failures: list[str] = []
    for event in events:
        if event.what == "FORK":
            _parent_pid, _parent_tgid, child_pid, _child_tgid = _fork_values(event)
            if child_pid in active:
                failures.append("active_pid_birth_duplicate")
            births_by_ordinal[event.ordinal] = generations.get(child_pid, 0)
            generations[child_pid] = generations.get(child_pid, 0) + 1
            active[child_pid] = event.ordinal
        elif event.what == "EXIT":
            process_pid, _process_tgid, _exit_code = _exit_values(event)
            active.pop(process_pid, None)
    return births_by_ordinal, failures


def _build_tree(
    events: list[_Event],
    roots: tuple[RoleSeed, ...],
) -> tuple[list[_Node], list[str]]:
    """Build role descendants from lifecycle metadata only."""

    ordered = sorted(events, key=_event_order)
    failures: list[str] = []
    birth_generations, birth_failures = _check_pid_births(ordered)
    failures.extend(birth_failures)
    fork_details: dict[int, tuple[int, int, int, int]] = {}
    for event in ordered:
        if event.what == "FORK":
            fork_details[event.ordinal] = _fork_values(event)
        elif event.what == "EXEC":
            _process_values(event)
        elif event.what == "EXIT":
            _exit_values(event)

    root_specs: dict[str, tuple[int, tuple[int, int, int, int]]] = {}
    seed_pid_set = {seed.pid for seed in roots}
    for seed in roots:
        candidates = [
            (event, fork_details[event.ordinal])
            for event in ordered
            if event.what == "FORK"
            and event.ordinal in fork_details
            and fork_details[event.ordinal][2] == seed.pid
            and event.timestamp_ns <= seed.captured_at_ns
        ]
        if not candidates:
            failures.append("seed_birth_missing")
            continue
        candidate, details = max(candidates, key=lambda item: _event_order(item[0]))
        if any(
            event.what == "EXIT"
            and _exit_values(event)[0] == seed.pid
            and _event_order(event) > _event_order(candidate)
            and event.timestamp_ns <= seed.captured_at_ns
            for event in ordered
        ):
            failures.append("seed_dead_before_capture")
        root_specs[seed.role] = (candidate.ordinal, details)

    if not root_specs:
        return [], failures

    root_tgids: dict[int, str] = {}
    for role, (_ordinal, details) in root_specs.items():
        child_tgid = details[3]
        prior = root_tgids.get(child_tgid)
        if prior is not None and prior != role:
            failures.append("role_tgid_collision")
        root_tgids[child_tgid] = role

    scope_start = min(
        event.timestamp_ns
        for event in ordered
        if event.ordinal in {spec[0] for spec in root_specs.values()}
    )
    nodes: list[_Node] = []
    active_by_pid: dict[int, _Node] = {}
    role_by_tgid: dict[int, str] = {}
    historical_role_by_tgid: dict[int, str] = {}
    root_by_ordinal = {
        ordinal: (role, details)
        for role, (ordinal, details) in root_specs.items()
    }
    for event in ordered:
        if event.what == "FORK":
            parent_pid, parent_tgid, child_pid, child_tgid = fork_details[event.ordinal]
            root_spec = root_by_ordinal.get(event.ordinal)
            if root_spec is not None:
                role, details = root_spec
                if child_pid in active_by_pid:
                    failures.append("active_pid_birth_duplicate")
                    continue
                node = _Node(
                    role=role,
                    pid=child_pid,
                    tgid=child_tgid,
                    generation=birth_generations.get(event.ordinal, 0),
                    parent_pid=parent_pid,
                    parent_tgid=parent_tgid,
                    root=True,
                    birth_ordinal=event.ordinal,
                )
                if len(nodes) >= MAX_NODES:
                    failures.append("node_count_exceeded")
                    break
                nodes.append(node)
                active_by_pid[child_pid] = node
                prior_role = role_by_tgid.get(child_tgid)
                if prior_role is not None and prior_role != role:
                    failures.append("role_tgid_collision")
                historical_role = historical_role_by_tgid.get(child_tgid)
                if historical_role is not None and historical_role != role:
                    failures.append("role_tgid_collision")
                role_by_tgid[child_tgid] = role
                historical_role_by_tgid[child_tgid] = role
                # ``details`` is deliberately only checked for binding; it is
                # never emitted as a provider-visible event row.
                if details != (parent_pid, parent_tgid, child_pid, child_tgid):
                    failures.append("root_binding_invalid")
                continue

            if child_pid in active_by_pid:
                failures.append("active_pid_birth_duplicate")
                continue

            child_role = role_by_tgid.get(child_tgid)
            parent_role = role_by_tgid.get(parent_tgid)
            if child_role is not None:
                if parent_role is not None and parent_role != child_role:
                    failures.append("role_tgid_collision")
            elif parent_role is not None:
                parent_node = active_by_pid.get(parent_pid)
                if parent_node is None or parent_node.tgid != parent_tgid:
                    failures.append("fork_parent_unknown")
                    continue
                child_role = parent_role
                if child_tgid in role_by_tgid and role_by_tgid[child_tgid] != child_role:
                    failures.append("role_tgid_collision")
                    continue
                role_by_tgid[child_tgid] = child_role
                historical_role_by_tgid[child_tgid] = child_role
            elif parent_tgid in historical_role_by_tgid:
                failures.append("fork_parent_unknown")
                continue
            else:
                # The connector can carry unrelated process forks.  They do
                # not enter a role tree unless a trusted parent/TGID binds it.
                continue

            node = _Node(
                role=child_role,
                pid=child_pid,
                tgid=child_tgid,
                generation=birth_generations.get(event.ordinal, 0),
                parent_pid=parent_pid,
                parent_tgid=parent_tgid,
                root=False,
                birth_ordinal=event.ordinal,
            )
            if len(nodes) >= MAX_NODES:
                failures.append("node_count_exceeded")
                break
            nodes.append(node)
            active_by_pid[child_pid] = node
            continue

        if event.what not in {"EXEC", "EXIT"} or event.timestamp_ns < scope_start:
            continue
        process_pid, process_tgid = _process_values(event)
        node = active_by_pid.get(process_pid)
        if node is None:
            if process_pid in seed_pid_set or process_tgid in role_by_tgid:
                failures.append("unknown_lifecycle_process")
            continue
        if node.tgid != process_tgid:
            failures.append("lifecycle_tgid_mismatch")
            continue
        if event.what == "EXEC":
            node.exec_count += 1
            continue
        if node.exit_count:
            failures.append("duplicate_exit")
            continue
        _pid, _tgid, exit_code = _exit_values(event)
        node.exit_count += 1
        node.exit_code = exit_code
        active_by_pid.pop(process_pid, None)
        if not any(item.tgid == node.tgid for item in active_by_pid.values()):
            role_by_tgid.pop(node.tgid, None)
        if node.root and exit_code != 0:
            failures.append("root_exit_nonzero")

    roots_by_role = {role: node for node in nodes if node.root for role in [node.role]}
    for seed in roots:
        root = roots_by_role.get(seed.role)
        if root is None:
            continue
        if root.exec_count < 1:
            failures.append("root_exec_missing")
        if root.exit_count < 1:
            failures.append("root_exit_missing")
    for node in nodes:
        if node.exit_count < 1:
            failures.append("descendant_exit_missing")
    return nodes, failures


def summarize_process_tree(
    records: Iterable[ProcessEvent],
    *,
    roots: Mapping[str, RoleSeed | Mapping[str, Any]],
    source_bound: bool | None = None,
    cpu_windows_closed: bool | None = None,
) -> dict[str, Any]:
    """Summarize one bounded process-event window into a non-formal receipt.

    The caller owns kernel subscription, sender binding, per-CPU sequence
    validation, and capture fences.  ``records`` is consumed through
    ``MAX_EVENTS + 1`` only, so an untrusted/infinite iterator cannot cause an
    unbounded list allocation.  Process identifiers remain internal to the
    graph/sequence digests and are never returned as numeric values.
    """

    failures = _check_external_gates(source_bound, cpu_windows_closed)
    normalized_roots: tuple[RoleSeed, ...] = ()
    events: list[_Event] = []
    nodes: list[_Node] = []
    try:
        normalized_roots = normalize_role_seeds(roots)
    except ProcessTreeMetadataGap as error:
        failures.append(error.code)

    if not failures or normalized_roots:
        try:
            raw_records = list(islice(records, MAX_EVENTS + 1))
        except (TypeError, ValueError):
            failures.append("records_invalid")
        else:
            if len(raw_records) > MAX_EVENTS:
                failures.append("event_count_exceeded")
            else:
                try:
                    events = [
                        _parse_event(item, ordinal)
                        for ordinal, item in enumerate(raw_records, 1)
                    ]
                except ProcessTreeMetadataGap as error:
                    failures.append(error.code)
                    events = []

    parse_failures = {
        "records_invalid",
        "event_count_exceeded",
        "event_invalid",
        "event_type_unknown",
        "event_values_invalid",
    }
    if normalized_roots and not events and not any(
        code in parse_failures for code in failures
    ):
        failures.append("records_empty")
    if normalized_roots and events and not any(code in parse_failures for code in failures):
        try:
            nodes, tree_failures = _build_tree(events, normalized_roots)
            failures.extend(tree_failures)
        except ProcessTreeMetadataGap as error:
            failures.append(error.code)

    status = "observed" if not failures else "gap"
    return _receipt(
        status=status,
        roots=normalized_roots,
        events=events,
        nodes=nodes,
        failures=failures,
    )


def validate_process_tree_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the closed, path-free receipt shape and self-digest."""

    expected = {
        "schema_version",
        "status",
        "formal_admission",
        "claim_eligible",
        "roles",
        "counts",
        "graph_sha256",
        "event_sequence_sha256",
        "failure_codes",
        "record_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ProcessTreeMetadataError("receipt fields are not closed")
    if value["schema_version"] != SCHEMA_VERSION:
        raise ProcessTreeMetadataError("receipt schema differs")
    if value["status"] not in {"observed", "gap"}:
        raise ProcessTreeMetadataError("receipt status is invalid")
    if value["formal_admission"] is not False or value["claim_eligible"] is not False:
        raise ProcessTreeMetadataError("receipt cannot grant admission")
    for name in ("graph_sha256", "event_sequence_sha256", "record_sha256"):
        digest = value[name]
        if not isinstance(digest, str) or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise ProcessTreeMetadataError(f"{name} is invalid")
    if value["record_sha256"] != _receipt_digest(value):
        raise ProcessTreeMetadataError("receipt digest differs")
    roles = value["roles"]
    if not isinstance(roles, list) or len(roles) > len(ROLES):
        raise ProcessTreeMetadataError("receipt roles are invalid")
    for item in roles:
        if not isinstance(item, Mapping) or set(item) != {
            "role",
            "root_pid_sha256",
            "pid_sha256",
            "counts",
        }:
            raise ProcessTreeMetadataError("receipt role projection is invalid")
        if item["role"] not in ROLES:
            raise ProcessTreeMetadataError("receipt role is invalid")
        for digest in [item["root_pid_sha256"], *item["pid_sha256"]]:
            if not isinstance(digest, str) or len(digest) != 64:
                raise ProcessTreeMetadataError("receipt PID digest is invalid")
        if not isinstance(item["pid_sha256"], list) or len(item["pid_sha256"]) > MAX_NODES:
            raise ProcessTreeMetadataError("receipt PID list is unbounded")
        counts = item["counts"]
        if not isinstance(counts, Mapping) or set(counts) != {"nodes", "forks", "execs", "exits"}:
            raise ProcessTreeMetadataError("receipt role counts are invalid")
        if any(
            isinstance(counts[name], bool)
            or not isinstance(counts[name], int)
            or counts[name] < 0
            for name in counts
        ):
            raise ProcessTreeMetadataError("receipt role counts are invalid")
    counts = value["counts"]
    if not isinstance(counts, Mapping) or set(counts) != {
        "events",
        "nodes",
        "forks",
        "execs",
        "exits",
    }:
        raise ProcessTreeMetadataError("receipt counts are invalid")
    if any(
        isinstance(counts[name], bool)
        or not isinstance(counts[name], int)
        or counts[name] < 0
        or (name == "events" and counts[name] > MAX_EVENTS)
        or (name == "nodes" and counts[name] > MAX_NODES)
        for name in counts
    ):
        raise ProcessTreeMetadataError("receipt counts are invalid")
    failures = value["failure_codes"]
    if not isinstance(failures, list) or any(not isinstance(code, str) for code in failures):
        raise ProcessTreeMetadataError("receipt failure codes are invalid")
    if failures != sorted(set(failures)):
        raise ProcessTreeMetadataError("receipt failure codes are not canonical")
    return dict(value)


aggregate_process_tree_metadata = summarize_process_tree
validate_metadata_receipt = validate_process_tree_receipt


__all__ = [
    "MAX_EVENTS",
    "MAX_NODES",
    "ROLES",
    "SCHEMA_VERSION",
    "ProcessTreeMetadataError",
    "ProcessTreeMetadataGap",
    "RoleSeed",
    "aggregate_process_tree_metadata",
    "canonical_json",
    "normalize_role_seeds",
    "pid_sha256",
    "sha256_hex",
    "summarize_process_tree",
    "validate_metadata_receipt",
    "validate_process_tree_receipt",
]
