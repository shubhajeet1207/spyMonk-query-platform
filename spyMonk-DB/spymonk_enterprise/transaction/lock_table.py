"""
Lock table with wound-wait deadlock avoidance (Spanner paper section 4.2.1).

Transactions are ordered by age = (start physical, start logical, txn_id).
On conflict: the requester wounds (aborts) strictly younger holders and waits
for older holders. Guarantees no deadlock without a waits-for graph.
"""

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Tuple


class TransactionAborted(Exception):
    """Transaction was wounded by an older transaction or timed out waiting."""


@dataclass
class _Lock:
    readers: Dict[str, tuple] = field(default_factory=dict)  # txn_id -> age
    writer: Optional[Tuple[str, tuple]] = None                # (txn_id, age)


class LockTable:
    def __init__(self, wait_timeout: float = 10.0):
        self.wait_timeout = wait_timeout
        self._locks: Dict[bytes, _Lock] = {}
        self._held: Dict[str, Set[bytes]] = defaultdict(set)
        self._wounded: Set[str] = set()
        self._committing: Set[str] = set()
        self._cond = threading.Condition()

    def is_wounded(self, txn_id: str) -> bool:
        with self._cond:
            return txn_id in self._wounded

    def enter_commit(self, txn_id: str) -> None:
        """Enter the un-woundable apply phase. Raises TransactionAborted if already
        wounded; otherwise fences this txn so concurrent conflicting requests WAIT for
        it (until it releases) instead of stripping its locks. A committing txn acquires
        no new locks, so waiting on it is deadlock-free."""
        with self._cond:
            if txn_id in self._wounded:
                raise TransactionAborted(f"transaction {txn_id} was wounded")
            self._committing.add(txn_id)

    def acquire_read(self, txn_id: str, age: tuple, key: bytes) -> None:
        deadline = time.monotonic() + self.wait_timeout
        with self._cond:
            while True:
                self._check_wounded(txn_id)
                lock = self._locks.setdefault(key, _Lock())
                if lock.writer is None or lock.writer[0] == txn_id:
                    lock.readers[txn_id] = age
                    self._held[txn_id].add(key)
                    return
                self._resolve_conflict(txn_id, age, [lock.writer], deadline)

    def acquire_write(self, txn_id: str, age: tuple, key: bytes) -> None:
        deadline = time.monotonic() + self.wait_timeout
        with self._cond:
            while True:
                self._check_wounded(txn_id)
                lock = self._locks.setdefault(key, _Lock())
                other_readers = [(t, a) for t, a in lock.readers.items() if t != txn_id]
                writer_conflict = lock.writer is not None and lock.writer[0] != txn_id
                if not other_readers and not writer_conflict:
                    lock.writer = (txn_id, age)
                    self._held[txn_id].add(key)
                    return
                holders = other_readers + ([lock.writer] if writer_conflict else [])
                self._resolve_conflict(txn_id, age, holders, deadline)

    def release_all(self, txn_id: str) -> None:
        with self._cond:
            self._release_locked(txn_id)
            self._wounded.discard(txn_id)
            self._committing.discard(txn_id)
            self._cond.notify_all()

    # -- internal (call with self._cond held) --------------------------------

    def _check_wounded(self, txn_id: str) -> None:
        if txn_id in self._wounded:
            raise TransactionAborted(f"transaction {txn_id} was wounded by an older transaction")

    def _resolve_conflict(self, txn_id: str, age: tuple, holders: list, deadline: float) -> None:
        younger = [h for h in holders if h[1] > age and h[0] not in self._committing]
        for holder_id, _ in younger:
            self._wounded.add(holder_id)
            self._release_locked(holder_id)
        if younger:
            self._cond.notify_all()
        if len(younger) == len(holders):
            return  # every conflict wounded; retry the acquire loop immediately
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TransactionAborted(f"transaction {txn_id} timed out waiting for lock")
        self._cond.wait(remaining)

    def _release_locked(self, txn_id: str) -> None:
        for key in self._held.pop(txn_id, set()):
            lock = self._locks.get(key)
            if lock is None:
                continue
            lock.readers.pop(txn_id, None)
            if lock.writer is not None and lock.writer[0] == txn_id:
                lock.writer = None
            if not lock.readers and lock.writer is None:
                self._locks.pop(key, None)
