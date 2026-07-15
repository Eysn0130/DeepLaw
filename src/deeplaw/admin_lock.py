from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar, cast

from .store import default_home

_Result = TypeVar("_Result")


@contextmanager
def _admin_lock(root: Path, name: str) -> Iterator[None]:
    if root.is_symlink():
        raise RuntimeError(f"DeepLaw home must not be a symbolic link: {root}")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not root.is_dir():
        raise RuntimeError(f"DeepLaw home is not a directory: {root}")
    lock_path = root / name
    if lock_path.is_symlink():
        raise RuntimeError(f"DeepLaw administration lock must not be a symbolic link: {lock_path}")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        os.chmod(lock_path, 0o600)
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def administration_locked(name: str) -> Callable[[Callable[..., _Result]], Callable[..., _Result]]:
    def decorate(function: Callable[..., _Result]) -> Callable[..., _Result]:
        @wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> _Result:
            configured_home = kwargs.get("home")
            root = (
                Path(configured_home).expanduser().absolute()
                if configured_home is not None
                else default_home().absolute()
            )
            with _admin_lock(root, name):
                return function(*args, **kwargs)

        return cast(Callable[..., _Result], wrapped)

    return decorate
