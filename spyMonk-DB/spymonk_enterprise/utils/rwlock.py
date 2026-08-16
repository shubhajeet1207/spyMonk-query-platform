"""Reader-writer lock with writer preference and context-manager API."""

import threading
from contextlib import contextmanager


class ReadWriteLock:
    def __init__(self):
        self._cond = threading.Condition()
        self._readers = 0
        self._writer = False
        self._writers_waiting = 0

    @contextmanager
    def read_locked(self):
        with self._cond:
            while self._writer or self._writers_waiting:
                self._cond.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._cond:
                self._readers -= 1
                if self._readers == 0:
                    self._cond.notify_all()

    @contextmanager
    def write_locked(self):
        with self._cond:
            self._writers_waiting += 1
            while self._writer or self._readers:
                self._cond.wait()
            self._writers_waiting -= 1
            self._writer = True
        try:
            yield
        finally:
            with self._cond:
                self._writer = False
                self._cond.notify_all()
