"""Reentrant, cross-process locks for JSON read/modify/write transactions."""

from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import os
import threading

_guard = threading.Lock()
_locks = {}
_local = threading.local()


@contextmanager
def file_transaction(path):
    key = os.path.normcase(str(Path(path).resolve()))
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_local, "held", None)
        if held is None:
            held = _local.held = set()
        if key in held:
            yield
            return
        lock_path = Path(key + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            if handle.seek(0, 2) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def locked_file(path_getter):
    """Decorate synchronous operations only; never hold a lock across await."""
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with file_transaction(path_getter()):
                return fn(*args, **kwargs)
        return wrapped
    return decorate
