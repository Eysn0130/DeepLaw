import hashlib
import json
import os
from unittest.mock import Mock

import pytest

from benchmarks.hosts.native_guest.bootstrap import approved_wheels, finish_route_observer


def test_only_frozen_wheel_inventory_is_selected(tmp_path):
    approved = tmp_path / "approved-1-py3-none-any.whl"
    approved.write_bytes(b"frozen")
    (tmp_path / "stale-0-py3-none-any.whl").write_bytes(b"unlisted stale input")
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps([{
        "name": approved.name, "sha256": hashlib.sha256(approved.read_bytes()).hexdigest(),
    }]))
    assert approved_wheels(tmp_path, inventory) == [approved]
    approved.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="wheel_digest_mismatch"):
        approved_wheels(tmp_path, inventory)


@pytest.mark.skipif(os.name != "posix", reason="native symlink boundary")
def test_wheel_inventory_does_not_follow_symlinks(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"frozen")
    link = tmp_path / "linked-1-py3-none-any.whl"
    link.symlink_to(target)
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps([{
        "name": link.name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }]))
    with pytest.raises(RuntimeError, match="wheel_file_invalid"):
        approved_wheels(tmp_path, inventory)


@pytest.mark.parametrize("already_exited", [True, False])
def test_route_finish_preserves_original_early_gap(already_exited):
    observer = Mock()
    observer.poll.return_value = 1 if already_exited else None
    observer.wait.return_value = 1
    if not already_exited:
        observer.stdin.write.side_effect = BrokenPipeError
        observer.poll.side_effect = [None, 1]
    gap = {"status": "gap", "failure": "tcp_port_gap", "formal_admission": False}
    read = Mock(return_value=gap)
    assert finish_route_observer([observer], read) == gap
    read.assert_called_once_with(observer.stdout)
    observer.wait.assert_called_once_with(timeout=2)
    if already_exited:
        observer.stdin.write.assert_not_called()
    observer.terminate.assert_not_called()


def test_route_finish_signals_live_observer_once():
    observer = Mock()
    observer.poll.side_effect = [None, 0]
    observer.wait.return_value = 0
    observed = {"status": "observed", "formal_admission": False}
    read = Mock(return_value=observed)
    assert finish_route_observer([observer], read) == observed
    observer.stdin.write.assert_called_once_with(b'{"op":"finish"}\n')
    observer.stdin.flush.assert_called_once_with()
    observer.wait.assert_called_once_with(timeout=2)
    observer.terminate.assert_not_called()


def test_route_finish_missing_output_is_a_finite_gap():
    observer = Mock()
    observer.poll.return_value = 1
    read = Mock(side_effect=RuntimeError("observer_eof"))
    assert finish_route_observer([observer], read) == {
        "status": "gap", "failure": "route_observer_failed", "formal_admission": False,
    }
    read.assert_called_once_with(observer.stdout)
    observer.stdin.write.assert_not_called()
