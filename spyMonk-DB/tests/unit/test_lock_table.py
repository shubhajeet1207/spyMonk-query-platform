import threading
import time

import pytest

from spymonk_enterprise.transaction.lock_table import LockTable, TransactionAborted


def age(n, txn_id):
    return (n, 0, txn_id)


def test_shared_reads_exclusive_writes():
    lt = LockTable(wait_timeout=0.2)
    lt.acquire_read("t1", age(1, "t1"), b"k")
    lt.acquire_read("t2", age(2, "t2"), b"k")  # readers share

    with pytest.raises(TransactionAborted):
        # t3 is younger than both readers -> must wait -> times out
        lt.acquire_write("t3", age(3, "t3"), b"k")


def test_older_writer_wounds_younger_holder():
    lt = LockTable(wait_timeout=1.0)
    lt.acquire_write("young", age(10, "young"), b"k")
    lt.acquire_write("old", age(1, "old"), b"k")   # wounds 'young', acquires immediately
    assert lt.is_wounded("young") is True
    with pytest.raises(TransactionAborted):
        lt.acquire_read("young", age(10, "young"), b"other")


def test_younger_waits_for_older_then_acquires():
    lt = LockTable(wait_timeout=2.0)
    lt.acquire_write("old", age(1, "old"), b"k")
    acquired = threading.Event()

    def younger():
        lt.acquire_write("young", age(5, "young"), b"k")
        acquired.set()

    t = threading.Thread(target=younger)
    t.start()
    time.sleep(0.1)
    assert not acquired.is_set()          # young is waiting, not wounded
    lt.release_all("old")
    t.join(timeout=2)
    assert acquired.is_set()


def test_release_all_clears_and_wakes():
    lt = LockTable(wait_timeout=1.0)
    lt.acquire_write("t1", age(1, "t1"), b"a")
    lt.acquire_read("t1", age(1, "t1"), b"b")
    lt.release_all("t1")
    lt.acquire_write("t2", age(2, "t2"), b"a")  # no conflict remains
