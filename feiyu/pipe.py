"""Deadline-aware reads from child-process pipes on Windows and Unix."""

import os
import queue
import select
import threading
import time


class PipeReader:
    def __init__(self, stream, *, threaded=None):
        self.stream = stream
        self.chunks = None
        self.stopped = threading.Event()
        if threaded if threaded is not None else os.name == "nt":
            self.chunks = queue.Queue(maxsize=16)
            threading.Thread(target=self._read, daemon=True).start()

    def _put(self, chunk):
        while not self.stopped.is_set():
            try:
                self.chunks.put(chunk, timeout=0.1)
                return
            except queue.Full:
                pass

    def _read(self):
        try:
            while not self.stopped.is_set():
                chunk = os.read(self.stream.fileno(), 65536)
                if not chunk:
                    break
                self._put(chunk)
        except (OSError, ValueError):
            pass
        finally:
            self._put(b"")

    def close(self):
        self.stopped.set()

    def read(self, deadline):
        left = deadline - time.monotonic()
        if left <= 0:
            return b""
        if self.chunks is not None:
            try:
                return self.chunks.get(timeout=left)
            except queue.Empty:
                return b""
        fd = self.stream.fileno()
        ready, _, _ = select.select([fd], [], [], left)
        return os.read(fd, 65536) if ready else b""
