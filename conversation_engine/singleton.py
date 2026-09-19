from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO

from .errors import SystemFailure


class SingletonLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle: BinaryIO | None = None

    def __enter__(self) -> "SingletonLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = self.path.open("r+b")
        except FileNotFoundError:
            handle = self.path.open("w+b")
        handle.seek(0)
        if handle.read(1) == b"":
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise SystemFailure("another Conversation Engine service owns the lock") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
        self.handle = handle
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                if os.name != "nt":
                    raise
        finally:
            self.handle.close()
            self.handle = None
