import pytest

from spymonk_enterprise.storage.engine.sstable import write_sstable, SSTableReader, SSTableCorruption
from spymonk_enterprise.time.hybrid_clock import Timestamp


def ts(p, l=0):
    return Timestamp(physical=p, logical=l)


@pytest.fixture
def table(tmp_path):
    path = tmp_path / "sst-00000001.sst"
    entries = [
        (b"apple", ts(100), False, b"a1"),
        (b"apple", ts(200), False, b"a2"),
        (b"banana", ts(150), False, b"b1"),
        (b"cherry", ts(100), True, None),   # tombstone
        (b"cherry", ts(50), False, b"c0"),
    ]
    write_sstable(path, entries)
    return path


def test_get_newest_version_at_or_below_read_ts(table):
    r = SSTableReader(table)
    assert r.get(b"apple", ts(300)) == (b"a2", False)
    assert r.get(b"apple", ts(150)) == (b"a1", False)
    assert r.get(b"apple", ts(50)) is None          # nothing that old
    assert r.get(b"missing", ts(300)) is None
    r.close()


def test_tombstones_are_returned_as_deleted(table):
    r = SSTableReader(table)
    assert r.get(b"cherry", ts(300)) == (None, True)
    assert r.get(b"cherry", ts(60)) == (b"c0", False)
    r.close()


def test_scan_range_and_order(table):
    r = SSTableReader(table)
    records = list(r.scan(b"apple", b"cherry"))
    keys = [rec[0] for rec in records]
    assert keys == [b"apple", b"apple", b"banana"]
    # Within a key: newest first
    assert records[0][1] > records[1][1]
    r.close()


def test_corruption_detected(tmp_path, table):
    raw = bytearray(table.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    bad = tmp_path / "bad.sst"
    bad.write_bytes(bytes(raw))
    with pytest.raises(SSTableCorruption):
        SSTableReader(bad)


def test_write_is_atomic_no_partial_files(tmp_path):
    write_sstable(tmp_path / "x.sst", [(b"k", ts(1), False, b"v")])
    assert [p.name for p in tmp_path.iterdir()] == ["x.sst"]


def test_hot_key_straddling_index_buckets(tmp_path):
    """70 versions of one key cross the INDEX_EVERY=64 boundary."""
    path = tmp_path / "hot.sst"
    entries = [(b"aaa", ts(1), False, b"first")]
    entries += [(b"k", ts(1000 + i), False, f"v{1000 + i}".encode()) for i in range(70)]
    entries += [(b"zzz", ts(1), False, b"last")]
    write_sstable(path, entries)
    r = SSTableReader(path)
    assert r.get(b"k", ts(2000)) == (b"v1069", False)      # newest, not 64-stale
    assert r.get(b"k", ts(1005)) == (b"v1005", False)      # mid-run read
    records = [rec for rec in r.scan(b"k", b"k\x00")]
    assert len(records) == 70                               # no versions dropped
    ts_list = [rec[1] for rec in records]
    assert ts_list == sorted(ts_list, reverse=True)         # newest-first
    r.close()


def test_many_distinct_keys_multi_bucket(tmp_path):
    path = tmp_path / "wide.sst"
    entries = [(f"key-{i:04d}".encode(), ts(10 + i), False, f"v{i}".encode())
               for i in range(200)]
    write_sstable(path, entries)
    r = SSTableReader(path)
    for i in (0, 63, 64, 65, 127, 128, 199):
        assert r.get(f"key-{i:04d}".encode(), ts(5000)) == (f"v{i}".encode(), False)
    assert r.get(b"key-0200", ts(5000)) is None
    assert r.get(b"a", ts(5000)) is None                    # before first index key
    r.close()


def test_footer_corruption_detected(tmp_path):
    path = tmp_path / "f.sst"
    write_sstable(path, [(b"k", ts(1), False, b"v")])
    raw = bytearray(path.read_bytes())
    raw[-12] ^= 0xFF                                        # flip a bit inside index_offset field
    bad = tmp_path / "bad-footer.sst"
    bad.write_bytes(bytes(raw))
    with pytest.raises(SSTableCorruption):
        SSTableReader(bad)
