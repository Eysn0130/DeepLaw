import hashlib
import json
import os

import pytest

from benchmarks.hosts.native_guest.bootstrap import approved_wheels


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
