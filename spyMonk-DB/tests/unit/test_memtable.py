"""
Unit tests for MemTable.
"""

import pytest
from spymonk_enterprise.storage.engine.memtable import MemTable
from spymonk_enterprise.time.hybrid_clock import Timestamp


def test_memtable_put_get():
    """Test basic put/get operations"""
    memtable = MemTable()

    ts = Timestamp(physical=1000, logical=0, uncertainty_ns=0)
    memtable.put(b"key1", b"value1", ts)

    value = memtable.get(b"key1")
    assert value == b"value1"


def test_memtable_mvcc():
    """Test MVCC - multiple versions of same key"""
    memtable = MemTable()

    ts1 = Timestamp(physical=1000, logical=0, uncertainty_ns=0)
    ts2 = Timestamp(physical=2000, logical=0, uncertainty_ns=0)
    ts3 = Timestamp(physical=3000, logical=0, uncertainty_ns=0)

    # Write multiple versions
    memtable.put(b"key", b"v1", ts1)
    memtable.put(b"key", b"v2", ts2)
    memtable.put(b"key", b"v3", ts3)

    # Read at different timestamps
    assert memtable.get(b"key", ts1) == b"v1"
    assert memtable.get(b"key", ts2) == b"v2"
    assert memtable.get(b"key", ts3) == b"v3"

    # Read latest
    assert memtable.get(b"key") == b"v3"


def test_memtable_delete():
    """Test delete (tombstone)"""
    memtable = MemTable()

    ts1 = Timestamp(physical=1000, logical=0, uncertainty_ns=0)
    ts2 = Timestamp(physical=2000, logical=0, uncertainty_ns=0)

    # Put then delete
    memtable.put(b"key", b"value", ts1)
    memtable.delete(b"key", ts2)

    # Reading at ts1 should return value
    assert memtable.get(b"key", ts1) == b"value"

    # Reading at ts2 or later should return None
    assert memtable.get(b"key", ts2) is None
    assert memtable.get(b"key") is None


def test_memtable_scan():
    """Test range scan"""
    memtable = MemTable()

    ts = Timestamp(physical=1000, logical=0, uncertainty_ns=0)

    # Insert multiple keys
    memtable.put(b"a", b"1", ts)
    memtable.put(b"b", b"2", ts)
    memtable.put(b"c", b"3", ts)
    memtable.put(b"d", b"4", ts)

    # Scan all
    results = list(memtable.scan())
    assert len(results) == 4

    # Scan range
    results = list(memtable.scan(start_key=b"b", end_key=b"d"))
    assert len(results) == 2
    assert results[0] == (b"b", b"2")
    assert results[1] == (b"c", b"3")


def test_memtable_size_tracking():
    """Test size tracking for flush trigger"""
    memtable = MemTable(max_size_bytes=100)

    ts = Timestamp(physical=1000, logical=0, uncertainty_ns=0)

    # Add entries until full
    for i in range(10):
        key = f"key{i}".encode()
        value = f"value{i}".encode()
        should_flush = memtable.put(key, value, ts)

        if should_flush:
            assert memtable.is_full
            break


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
