"""
Multi-Version Concurrency Control (MVCC) Store.

LSM layout: WAL (durability) -> MemTable (recent writes) -> SSTables (flushed,
immutable). Reads merge MemTable first, then SSTables newest-to-oldest.
"""

import threading
from typing import Optional, Iterator, Tuple, List
from pathlib import Path
import logging

from spymonk_enterprise.time.hybrid_clock import Timestamp, HybridLogicalClock
from spymonk_enterprise.storage.engine.memtable import MemTable
from spymonk_enterprise.storage.engine.wal import WriteAheadLog
from spymonk_enterprise.storage.engine.sstable import write_sstable, SSTableReader, SSTableCorruption

logger = logging.getLogger(__name__)


class MVCCStore:
    def __init__(self, data_dir: Path, clock: HybridLogicalClock,
                 memtable_size_bytes: int = 4 * 1024 * 1024,
                 compaction_threshold: int = 8,
                 gc_retention_ns: int = 3_600_000_000_000):
        self.data_dir = Path(data_dir)
        self.clock = clock
        self.compaction_threshold = compaction_threshold
        self.gc_retention_ns = gc_retention_ns

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sst_dir = self.data_dir / 'sst'
        self.sst_dir.mkdir(exist_ok=True)

        self.wal = WriteAheadLog(self.data_dir / 'wal')
        self.memtable = MemTable(max_size_bytes=memtable_size_bytes)
        self.memtable_size_bytes = memtable_size_bytes
        self.sstables: List[SSTableReader] = []   # newest first
        self.sstable_seq = 0

        self._lock = threading.Lock()   # guards flush/compact/sstable list swaps
        self._load_sstables()
        self._recover()
        logger.info(f"Initialized MVCC store in {data_dir} ({len(self.sstables)} SSTables)")

    # -- writes ------------------------------------------------------------

    def put(self, key: bytes, value: bytes, timestamp: Optional[Timestamp] = None) -> Timestamp:
        if timestamp is None:
            timestamp = self.clock.now()
        # WAL append + memtable insert must be atomic w.r.t. flush()'s
        # lock-held snapshot/clear/checkpoint, or a write landing in between
        # is wiped from the memtable and its WAL segment deleted.
        with self._lock:
            self.wal.append(timestamp, key, value, deleted=False)
            should_flush = self.memtable.put(key, value, timestamp)
        if should_flush:   # outside the lock: flush() re-acquires it (non-reentrant)
            self.flush()
        return timestamp

    def delete(self, key: bytes, timestamp: Optional[Timestamp] = None) -> Timestamp:
        if timestamp is None:
            timestamp = self.clock.now()
        with self._lock:
            self.wal.append(timestamp, key, None, deleted=True)
            should_flush = self.memtable.delete(key, timestamp)
        if should_flush:   # outside the lock: flush() re-acquires it (non-reentrant)
            self.flush()
        return timestamp

    # -- reads -------------------------------------------------------------

    def get(self, key: bytes, timestamp: Optional[Timestamp] = None) -> Optional[bytes]:
        if timestamp is None:
            timestamp = self.clock.now()
        entry = self.memtable.get_entry(key, timestamp)
        if entry is not None:
            return None if entry.deleted else entry.value
        while True:
            result, ok = self._get_from_readers(list(self.sstables), key, timestamp)
            if ok:
                return result
            # a compaction closed a reader mid-read; retry with the new list

    def _get_from_readers(self, readers: List[SSTableReader], key: bytes,
                          timestamp: Timestamp) -> Tuple[Optional[bytes], bool]:
        for reader in readers:
            if not reader.acquire():
                return None, False
            try:
                hit = reader.get(key, timestamp)
            finally:
                reader.release()
            if hit is not None:
                value, deleted = hit
                return (None if deleted else value), True
        return None, True

    def scan(self, start_key: Optional[bytes] = None, end_key: Optional[bytes] = None,
             timestamp: Optional[Timestamp] = None) -> Iterator[Tuple[bytes, bytes]]:
        if timestamp is None:
            timestamp = self.clock.now()
        while True:
            records = [(e.key, e.timestamp, e.deleted, e.value)
                       for e in self.memtable.scan_entries(start_key, end_key)]
            stale = False
            for reader in list(self.sstables):
                if not reader.acquire():
                    stale = True   # a compaction closed this reader; retry with the new list
                    break
                try:
                    records.extend(reader.scan(start_key, end_key))
                finally:
                    reader.release()
            if not stale:
                break
        records.sort(key=lambda r: (r[0], -r[1].physical, -r[1].logical))

        current_key = None
        resolved = False
        for key, rts, deleted, value in records:
            if key != current_key:
                current_key, resolved = key, False
            if resolved or rts > timestamp:
                continue
            resolved = True
            if not deleted:
                yield key, value

    # -- flush / compaction / recovery --------------------------------------

    def flush(self):
        """Write MemTable to a new SSTable, THEN clear it. Never drops data."""
        with self._lock:
            entries = self.memtable.snapshot()
            if not entries:
                return
            self.sstable_seq += 1
            path = self.sst_dir / f"sst-{self.sstable_seq:08d}.sst"
            write_sstable(path, [(e.key, e.timestamp, e.deleted, e.value) for e in entries])
            self.sstables.insert(0, SSTableReader(path))
            self.memtable.clear()
            self.wal.checkpoint()
            logger.info(f"Flushed {len(entries)} entries to {path.name}")
        if len(self.sstables) > self.compaction_threshold:
            self.compact()

    def compact(self):
        """Merge all SSTables into one, dropping versions older than the GC watermark."""
        with self._lock:
            if len(self.sstables) <= 1:
                return
            watermark = self.clock.now().physical - self.gc_retention_ns
            records = []
            for reader in self.sstables:
                records.extend(reader.scan(None, None))
            records.sort(key=lambda r: (r[0], -r[1].physical, -r[1].logical))

            kept = []
            current_key = None
            newest_for_key = False
            prev_ident = None
            for key, rts, deleted, value in records:
                # Skip byte-identical duplicates (records are sorted, so they
                # are adjacent). Assumes commit timestamps are unique per
                # (key, ts) - the transaction layer guarantees monotonic
                # unique timestamps, so an identical (key, physical, logical)
                # can only be the same record re-replayed (e.g. after a crash
                # between flush and checkpoint), never two distinct writes.
                ident = (key, rts.physical, rts.logical)
                if ident == prev_ident:
                    continue
                prev_ident = ident
                if key != current_key:
                    current_key, newest_for_key = key, True
                if newest_for_key:
                    # Newest version: keep, unless it's a tombstone older than the watermark.
                    if not (deleted and rts.physical < watermark):
                        kept.append((key, rts, deleted, value))
                    newest_for_key = False
                elif rts.physical >= watermark:
                    kept.append((key, rts, deleted, value))

            self.sstable_seq += 1
            path = self.sst_dir / f"sst-{self.sstable_seq:08d}.sst"
            write_sstable(path, kept)
            old = self.sstables
            self.sstables = [SSTableReader(path)]
            for reader in old:
                # POSIX: the open fd stays valid after unlink, so in-flight
                # reads holding a reference finish safely; the fd closes when
                # the last reference is released (refcounted readers).
                reader.path.unlink(missing_ok=True)
                reader.close()   # drops owner ref; file closes when last in-flight read releases
            logger.info(f"Compacted {len(old)} SSTables -> {path.name} ({len(kept)} records)")

    def _load_sstables(self):
        paths = sorted(self.sst_dir.glob('sst-*.sst'), reverse=True)  # newest first
        for p in paths:
            try:
                seq = int(p.stem.split('-')[1])
            except (IndexError, ValueError):
                continue
            self.sstable_seq = max(self.sstable_seq, seq)
            try:
                self.sstables.append(SSTableReader(p))
            except SSTableCorruption:
                logger.error(f"Skipping corrupt SSTable {p}")

    def _recover(self):
        count = 0
        for entry in self.wal.replay():
            if entry.deleted:
                self.memtable.delete(entry.key, entry.timestamp)
            else:
                self.memtable.put(entry.key, entry.value, entry.timestamp)
            count += 1
        logger.info(f"Recovered {count} entries from WAL")

    def close(self):
        self.wal.close()
        for reader in self.sstables:
            reader.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
