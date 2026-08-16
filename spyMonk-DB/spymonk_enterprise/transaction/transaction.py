"""
Transaction Manager — Spanner-style concurrency control (paper section 4).

- RW transactions: two-phase locking with wound-wait; commit timestamp
  s >= TT.now().latest and strictly greater than any previously assigned
  timestamp (monotonicity); commit-wait until TT.after(s) BEFORE applying
  (4.2.1); t_safe advances after apply.
- RO transactions: predeclared, lock-free, snapshot at s_read = LastTS()
  (single-group optimization, 4.2.2).
- Snapshot reads: client timestamp t, served once t <= t_safe (4.1.3).
"""

import uuid
import threading
from enum import Enum
from typing import Optional, Dict, List
from dataclasses import dataclass
import logging

from spymonk_enterprise.time.hybrid_clock import Timestamp, HybridLogicalClock
from spymonk_enterprise.time.truetime import TrueTime
from spymonk_enterprise.storage.mvcc import MVCCStore
from spymonk_enterprise.transaction.lock_table import LockTable, TransactionAborted

logger = logging.getLogger(__name__)

__all__ = ["TransactionManager", "Transaction", "TransactionType", "TransactionState",
           "TransactionAborted", "TransactionError", "SafeTimeTimeout", "Mutation"]


class TransactionError(Exception):
    """Generic transaction failure."""


class SafeTimeTimeout(TransactionError):
    """Snapshot timestamp is ahead of t_safe and did not become safe in time."""


class TransactionType(Enum):
    READ_WRITE = "read_write"
    READ_ONLY = "read_only"


class TransactionState(Enum):
    ACTIVE = "active"
    PREPARING = "preparing"
    COMMITTED = "committed"
    ABORTED = "aborted"


@dataclass
class Mutation:
    key: bytes
    value: Optional[bytes]
    is_delete: bool = False


class Transaction:
    def __init__(self, txn_id: str, txn_type: TransactionType,
                 start_timestamp: Timestamp, manager: 'TransactionManager'):
        self.txn_id = txn_id
        self.type = txn_type
        self.start_timestamp = start_timestamp
        self.manager = manager
        self.state = TransactionState.ACTIVE
        self.age = (start_timestamp.physical, start_timestamp.logical, txn_id)
        self.mutations: List[Mutation] = []
        self.commit_timestamp: Optional[Timestamp] = None

    def get(self, key: bytes) -> Optional[bytes]:
        if self.state != TransactionState.ACTIVE:
            raise TransactionError(f"Transaction {self.txn_id} is not active")

        if self.type == TransactionType.READ_ONLY:
            return self.manager.store.get(key, self.start_timestamp)

        # Read-your-writes from the buffer.
        for mutation in reversed(self.mutations):
            if mutation.key == key:
                return None if mutation.is_delete else mutation.value

        # 2PL: RW reads take an EXCLUSIVE lock (pessimistic) so two RW txns
        # can never both read the same pre-image and both commit -> no lost
        # updates. RO txns stay lock-free via MVCC snapshots.
        self.manager.lock_table.acquire_write(self.txn_id, self.age, key)
        # Defensive floor: commit timestamps run at TT.now().latest, which can
        # in principle lead a raw HLC reading. Flooring the read timestamp at
        # last_commit_ts guarantees a locked read always observes the latest
        # committed version, independent of clock configuration. (Exclusive
        # read locks already serialize RW readers; this is belt-and-braces.)
        read_ts = self.manager.clock.now()
        last_commit_ts = self.manager.last_commit_ts
        if last_commit_ts is not None and last_commit_ts > read_ts:
            read_ts = last_commit_ts
        return self.manager.store.get(key, read_ts)

    def put(self, key: bytes, value: bytes):
        self._check_writable()
        self.mutations.append(Mutation(key=key, value=value, is_delete=False))

    def delete(self, key: bytes):
        self._check_writable()
        self.mutations.append(Mutation(key=key, value=None, is_delete=True))

    def commit(self) -> bool:
        return self.manager.commit(self)

    def abort(self):
        self.manager.abort(self)

    def _check_writable(self):
        if self.state != TransactionState.ACTIVE:
            raise TransactionError(f"Transaction {self.txn_id} is not active")
        if self.type == TransactionType.READ_ONLY:
            raise TransactionError("Cannot write in read-only transaction")


class TransactionManager:
    def __init__(self, clock: HybridLogicalClock, store: MVCCStore,
                 truetime: Optional[TrueTime] = None,
                 lock_wait_timeout: float = 10.0):
        self.clock = clock
        self.store = store
        self.truetime = truetime or TrueTime(clock)
        self.lock_table = LockTable(wait_timeout=lock_wait_timeout)

        self.active_txns: Dict[str, Transaction] = {}
        self.last_commit_ts: Optional[Timestamp] = None   # LastTS() == t_safe
        self._last_assigned: Optional[Timestamp] = None   # monotonicity invariant
        self._lock = threading.Lock()
        self._safe_cond = threading.Condition()

        logger.info("Initialized transaction manager (2PL + wound-wait + commit-wait)")

    def begin(self, read_only: bool = False) -> Transaction:
        txn_id = str(uuid.uuid4())
        if read_only:
            # LastTS() optimization: latest committed data, trivially externally
            # consistent for a single group (4.2.2).
            start = self.last_commit_ts or self.clock.now()
            txn_type = TransactionType.READ_ONLY
        else:
            start = self.clock.now()
            txn_type = TransactionType.READ_WRITE

        txn = Transaction(txn_id, txn_type, start, self)
        with self._lock:
            self.active_txns[txn_id] = txn
        return txn

    def get_txn(self, txn_id: str) -> Optional[Transaction]:
        """Look up an active transaction under the manager lock."""
        with self._lock:
            return self.active_txns.get(txn_id)

    def commit(self, txn: Transaction) -> bool:
        if txn.state != TransactionState.ACTIVE:
            logger.warning(f"Transaction {txn.txn_id} already finalized")
            return False

        if txn.type == TransactionType.READ_ONLY:
            txn.state = TransactionState.COMMITTED
            self._finalize(txn)
            return True

        try:
            txn.state = TransactionState.PREPARING
            if self.lock_table.is_wounded(txn.txn_id):
                raise TransactionAborted(f"transaction {txn.txn_id} was wounded")

            # Acquire write locks in sorted key order (reduces contention churn).
            for key in sorted({m.key for m in txn.mutations}):
                self.lock_table.acquire_write(txn.txn_id, txn.age, key)

            s = self._choose_commit_timestamp()
            txn.commit_timestamp = s

            # Commit wait BEFORE making data visible (4.2.1).
            self.truetime.commit_wait(s)

            # Atomically verify we were not wounded and enter the un-woundable apply
            # phase: from here until release, no older txn can strip our locks -- it
            # will wait for us instead (4.2.1). Closes the read-after-wound window.
            self.lock_table.enter_commit(txn.txn_id)

            for m in txn.mutations:
                if m.is_delete:
                    self.store.delete(m.key, s)
                else:
                    self.store.put(m.key, m.value, s)

            self._advance_safe_time(s)
            txn.state = TransactionState.COMMITTED
            return True

        except TransactionAborted:
            txn.state = TransactionState.ABORTED
            raise
        except Exception:
            txn.state = TransactionState.ABORTED
            raise
        finally:
            self._finalize(txn)

    def abort(self, txn: Transaction):
        txn.state = TransactionState.ABORTED
        self._finalize(txn)

    def read_at(self, key: bytes, ts: Timestamp, timeout: float = 5.0) -> Optional[bytes]:
        """Snapshot read at a client-provided timestamp (4.1.3): wait for t_safe."""
        with self._safe_cond:
            ok = self._safe_cond.wait_for(
                lambda: self.last_commit_ts is not None and self.last_commit_ts >= ts,
                timeout=timeout)
            if not ok:
                raise SafeTimeTimeout(f"t_safe has not reached {ts}")
        return self.store.get(key, ts)

    # -- internal ------------------------------------------------------------

    def _choose_commit_timestamp(self) -> Timestamp:
        """s >= TT.now().latest, strictly greater than anything assigned before."""
        with self._lock:
            hlc = self.clock.now()
            physical = max(hlc.physical, self.truetime.now().latest)
            logical = hlc.logical
            prev = self._last_assigned
            if prev is not None and (physical, logical) <= (prev.physical, prev.logical):
                physical, logical = prev.physical, prev.logical + 1
            s = Timestamp(physical=physical, logical=logical,
                          uncertainty_ns=hlc.uncertainty_ns)
            self._last_assigned = s
            return s

    def _advance_safe_time(self, s: Timestamp):
        with self._safe_cond:
            if self.last_commit_ts is None or s > self.last_commit_ts:
                self.last_commit_ts = s
            self._safe_cond.notify_all()

    def _finalize(self, txn: Transaction):
        self.lock_table.release_all(txn.txn_id)
        with self._lock:
            self.active_txns.pop(txn.txn_id, None)
