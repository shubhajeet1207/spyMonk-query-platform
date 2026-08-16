# tests/unit/test_transactions.py
import threading

import pytest

from spymonk_enterprise.storage.mvcc import MVCCStore
from spymonk_enterprise.time.hybrid_clock import HybridLogicalClock, Timestamp
from spymonk_enterprise.time.truetime import MockTrueTime
from spymonk_enterprise.transaction.transaction import (
    TransactionManager, TransactionAborted, SafeTimeTimeout)


@pytest.fixture
def manager(tmp_path):
    clock = HybridLogicalClock("t-node")
    store = MVCCStore(tmp_path / "db", clock)
    return TransactionManager(clock, store, truetime=MockTrueTime(eps_ns=1_000))


def test_regression_no_lost_updates(manager):
    """P0 #3: concurrent read-modify-write increments must serialize under 2PL."""
    manager.store.put(b"counter", b"0")
    errors = []

    def increment():
        for _ in range(20):
            while True:
                txn = manager.begin()
                try:
                    val = int(txn.get(b"counter") or b"0")
                    txn.put(b"counter", str(val + 1).encode())
                    txn.commit()
                    break
                except TransactionAborted:
                    continue  # wounded: retry
                except Exception as e:  # pragma: no cover
                    errors.append(e)
                    break

    threads = [threading.Thread(target=increment) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert not errors
    # Read via a fresh RO txn (LastTS() snapshot), not a bare manager.store.get()
    # with no timestamp: MockTrueTime's commit_wait floor (100us/commit, faked via
    # counter-advance rather than real sleep) drifts its clock ahead of the raw
    # HLC (clock.now()) over many commits. A raw store.get() with no timestamp
    # defaults to that raw HLC reading and can miss the most recent commits --
    # an artifact of the mock clock, reproducible even single-threaded with zero
    # lock contention, and unrelated to the lost-update invariant under test.
    verify = manager.begin(read_only=True)
    assert int(verify.get(b"counter")) == 80


def test_commit_timestamps_strictly_monotonic(manager):
    seen = []
    for i in range(10):
        txn = manager.begin()
        txn.put(f"k{i}".encode(), b"v")
        txn.commit()
        seen.append(txn.commit_timestamp)
    for a, b in zip(seen, seen[1:]):
        assert b > a


def test_commit_wait_is_enforced(manager):
    txn = manager.begin()
    txn.put(b"k", b"v")
    txn.commit()
    assert len(manager.truetime.waited) >= 1  # MockTrueTime recorded a commit wait
    assert manager.truetime.after(txn.commit_timestamp)


def test_read_only_txn_sees_stable_snapshot(manager):
    manager.store.put(b"a", b"1")
    w = manager.begin()
    w.put(b"a", b"2")

    ro = manager.begin(read_only=True)
    first = ro.get(b"a")
    w.commit()                       # commits while RO is open
    second = ro.get(b"a")
    assert first == second == b"1"   # snapshot at s_read = LastTS()


def test_snapshot_read_blocks_until_safe_time(manager):
    txn = manager.begin()
    txn.put(b"x", b"1")
    txn.commit()
    s = txn.commit_timestamp
    assert manager.read_at(b"x", s) == b"1"
    future = Timestamp(physical=s.physical + 10_000_000_000, logical=0)
    with pytest.raises(SafeTimeTimeout):
        manager.read_at(b"x", future, timeout=0.2)


def test_write_in_read_only_txn_rejected(manager):
    ro = manager.begin(read_only=True)
    with pytest.raises(Exception):
        ro.put(b"k", b"v")


def test_no_lost_updates_high_contention(manager):
    """8 threads x 25 increments on one key, all serialized by exclusive 2PL."""
    import threading
    manager.store.put(b"ctr", b"0")
    errors = []

    def worker():
        for _ in range(25):
            while True:
                txn = manager.begin()
                try:
                    v = int(txn.get(b"ctr") or b"0")
                    txn.put(b"ctr", str(v + 1).encode())
                    txn.commit()
                    break
                except TransactionAborted:
                    continue
                except Exception as e:  # noqa: BLE001
                    errors.append(e)
                    return

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors, f"unexpected error: {errors[0]!r}"
    # Safe read (see test_regression_no_lost_updates for why not manager.store.get()
    # with no timestamp -- same MockTrueTime/raw-HLC clock-drift artifact).
    verify = manager.begin(read_only=True)
    assert int(verify.get(b"ctr")) == 200


def test_apply_phase_exception_marks_aborted_and_releases(manager):
    txn = manager.begin()
    txn.put(b"k", b"v")
    original_put = manager.store.put
    manager.store.put = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("disk full"))
    try:
        with pytest.raises(RuntimeError):
            txn.commit()
    finally:
        manager.store.put = original_put
    from spymonk_enterprise.transaction.transaction import TransactionState
    assert txn.state == TransactionState.ABORTED
    # Locks were released: a new txn can take the same key immediately.
    txn2 = manager.begin()
    txn2.put(b"k", b"v2")
    assert txn2.commit() is True
