from spymonk_enterprise.storage.mvcc import MVCCStore
from spymonk_enterprise.time.hybrid_clock import HybridLogicalClock


def make_store(tmp_path, **kw):
    clock = HybridLogicalClock("test-node")
    return MVCCStore(tmp_path / "db", clock, **kw)


def test_regression_no_data_loss_across_flush(tmp_path):
    """P0 #1: keys written before a flush must remain readable without restart."""
    store = make_store(tmp_path, memtable_size_bytes=1024)  # tiny → many flushes
    for i in range(200):
        store.put(f"key-{i:04d}".encode(), f"val-{i}".encode())
    for i in range(200):
        assert store.get(f"key-{i:04d}".encode()) == f"val-{i}".encode(), f"lost key-{i:04d}"
    store.close()


def test_recovery_after_restart(tmp_path):
    store = make_store(tmp_path, memtable_size_bytes=1024)
    for i in range(100):
        store.put(f"key-{i:04d}".encode(), b"v")
    store.delete(b"key-0007")
    store.close()

    store2 = make_store(tmp_path, memtable_size_bytes=1024)
    assert store2.get(b"key-0003") == b"v"
    assert store2.get(b"key-0007") is None
    assert store2.get(b"key-0099") == b"v"
    store2.close()


def test_scan_merges_memtable_and_sstables(tmp_path):
    store = make_store(tmp_path, memtable_size_bytes=1024)
    for i in range(60):
        store.put(f"a-{i:03d}".encode(), b"old")
    store.flush()
    store.put(b"a-005", b"new")          # newer version in memtable
    store.delete(b"a-010")               # tombstone in memtable
    rows = dict(store.scan(b"a-", b"a-\xff"))
    assert rows[b"a-005"] == b"new"
    assert b"a-010" not in rows
    assert rows[b"a-000"] == b"old"
    assert len(rows) == 59
    store.close()


def test_compaction_reduces_files_and_keeps_data(tmp_path):
    store = make_store(tmp_path, memtable_size_bytes=512, compaction_threshold=3)
    for i in range(300):
        store.put(f"k-{i:04d}".encode(), f"v{i}".encode())
    assert len(store.sstables) <= 3 + 1  # threshold respected via auto-compaction
    for i in range(0, 300, 37):
        assert store.get(f"k-{i:04d}".encode()) == f"v{i}".encode()
    store.close()


def test_restart_after_flush_does_not_replay_flushed_wal(tmp_path):
    store = make_store(tmp_path, memtable_size_bytes=1024)
    for i in range(100):
        store.put(f"key-{i:04d}".encode(), b"v")
    store.flush()
    store.close()

    store2 = make_store(tmp_path, memtable_size_bytes=1024)
    assert len(store2.memtable) == 0            # flushed WAL not re-replayed
    assert store2.get(b"key-0042") == b"v"      # served from SSTables
    store2.close()


def test_concurrent_reads_survive_compaction(tmp_path):
    import threading
    store = make_store(tmp_path, memtable_size_bytes=512, compaction_threshold=2)
    for i in range(50):
        store.put(f"k-{i:03d}".encode(), f"v{i}".encode())
    store.flush()

    errors = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                assert store.get(b"k-025") == b"v25"
                list(store.scan(b"k-", b"k-\xff"))
            except Exception as e:  # noqa: BLE001
                errors.append(e)
                return

    t = threading.Thread(target=reader)
    t.start()
    try:
        for rnd in range(20):
            for i in range(30):
                store.put(f"extra-{rnd:02d}-{i:02d}".encode(), b"x")
            store.flush()
            store.compact()
    finally:
        stop.set()
        t.join(timeout=10)
    assert not errors, f"concurrent read failed: {errors[0]!r}"
    store.close()


def test_compaction_dedupes_identical_records(tmp_path):
    store = make_store(tmp_path, memtable_size_bytes=1024)
    ts = store.clock.now()
    store.put(b"dup", b"v", ts)
    store.flush()
    store.memtable.put(b"dup", b"v", ts)   # simulate a re-replayed duplicate
    store.flush()
    store.compact()
    assert store.sstables[0].entry_count == 1


def test_concurrent_writers_never_lose_acknowledged_writes(tmp_path):
    import threading
    store = make_store(tmp_path, memtable_size_bytes=512)
    errors = []

    def writer(wid):
        try:
            for i in range(200):
                store.put(f"w{wid}-{i:04d}".encode(), b"v")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(w,)) for w in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors
    missing = [f"w{w}-{i:04d}" for w in range(8) for i in range(200)
               if store.get(f"w{w}-{i:04d}".encode()) is None]
    assert not missing, f"lost {len(missing)} live keys, e.g. {missing[:5]}"
    store.close()

    store2 = make_store(tmp_path, memtable_size_bytes=512)
    missing2 = [f"w{w}-{i:04d}" for w in range(8) for i in range(200)
                if store2.get(f"w{w}-{i:04d}".encode()) is None]
    assert not missing2, f"lost {len(missing2)} keys after restart"
    store2.close()


def test_compaction_closes_old_readers(tmp_path):
    store = make_store(tmp_path, memtable_size_bytes=512, compaction_threshold=2)
    for i in range(150):
        store.put(f"k-{i:03d}".encode(), b"v")
    store.flush()
    if len(store.sstables) < 2:   # auto-compaction may have just merged to 1;
        store.put(b"pad", b"v")   # guarantee the explicit compact has work to do
        store.flush()
    old = [r for r in store.sstables]
    assert len(old) >= 2
    store.compact()
    gone = [r for r in old if r not in store.sstables]
    assert gone, "compaction should have replaced at least one reader"
    assert all(r._closed for r in gone)      # no in-flight reads -> closed immediately
    assert store.get(b"k-075") == b"v"       # reads still correct post-compaction
    store.close()
