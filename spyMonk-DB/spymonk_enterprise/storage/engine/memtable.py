"""
MemTable: In-memory write buffer for LSM-Tree

The MemTable is the first place writes go. It's an in-memory
sorted structure that allows fast writes and reads.

When it reaches a size threshold, it's flushed to disk as an SSTable.
"""

import threading
from typing import Optional, Iterator, Tuple
from collections.abc import MutableMapping
import bisect

from spymonk_enterprise.time.hybrid_clock import Timestamp


class MemTableEntry:
    """Single entry in MemTable with MVCC support"""

    __slots__ = ('key', 'value', 'timestamp', 'deleted')

    def __init__(self, key: bytes, value: Optional[bytes], timestamp: Timestamp, deleted: bool = False):
        self.key = key
        self.value = value
        self.timestamp = timestamp
        self.deleted = deleted

    def __lt__(self, other):
        """Sort by key, then by timestamp (descending for newest first)"""
        if self.key != other.key:
            return self.key < other.key
        # Newer timestamps first
        return self.timestamp > other.timestamp


class MemTable:
    """
    In-memory sorted table for recent writes.

    Uses a sorted list for simplicity (could use skip list or B-tree for better performance).
    Thread-safe with read-write lock.

    Features:
    - Sorted by key for range scans
    - MVCC: Multiple versions per key
    - Thread-safe operations
    - Size tracking for flush triggers
    """

    def __init__(self, max_size_bytes: int = 4 * 1024 * 1024):  # 4MB default
        self.max_size_bytes = max_size_bytes
        self.size_bytes = 0
        self.entries: list[MemTableEntry] = []

        # Thread safety
        self._lock = threading.Lock()

    def put(self, key: bytes, value: bytes, timestamp: Timestamp) -> bool:
        """
        Insert key-value pair.

        Returns:
            True if MemTable should be flushed (size exceeded)
        """
        entry = MemTableEntry(key, value, timestamp, deleted=False)

        with self._lock:
            # Binary search to find insertion point
            bisect.insort(self.entries, entry)

            # Update size estimate
            self.size_bytes += len(key) + len(value) + 64  # 64 bytes overhead

            return self.size_bytes >= self.max_size_bytes

    def delete(self, key: bytes, timestamp: Timestamp) -> bool:
        """
        Mark key as deleted (tombstone).

        In MVCC, we don't actually remove the key, we just mark it deleted.

        Returns:
            True if MemTable should be flushed (size exceeded)
        """
        entry = MemTableEntry(key, None, timestamp, deleted=True)

        with self._lock:
            bisect.insort(self.entries, entry)
            self.size_bytes += len(key) + 64
            return self.size_bytes >= self.max_size_bytes

    def get(self, key: bytes, timestamp: Optional[Timestamp] = None) -> Optional[bytes]:
        """
        Get value for key at given timestamp.

        If timestamp is None, return latest version.

        Returns:
            Value if found and not deleted, None otherwise
        """
        with self._lock:
            # Binary search for key
            idx = self._find_key_index(key)

            if idx == -1:
                return None

            # Scan forward to find appropriate version
            for entry in self.entries[idx:]:
                if entry.key != key:
                    break

                # Check timestamp
                if timestamp is None or entry.timestamp <= timestamp:
                    return None if entry.deleted else entry.value

            return None

    def scan(self, start_key: Optional[bytes] = None,
             end_key: Optional[bytes] = None,
             timestamp: Optional[Timestamp] = None) -> Iterator[Tuple[bytes, bytes]]:
        """
        Range scan from start_key to end_key (exclusive).

        Yields:
            (key, value) tuples
        """
        with self._lock:
            entries = self.entries[:]

        seen_keys = set()

        for entry in entries:
            # Check key range
            if start_key and entry.key < start_key:
                continue
            if end_key and entry.key >= end_key:
                break

            # Skip if we've already returned this key (newer version)
            if entry.key in seen_keys:
                continue

            # Check timestamp
            if timestamp and entry.timestamp > timestamp:
                continue

            # Skip deleted entries
            if entry.deleted:
                seen_keys.add(entry.key)
                continue

            yield (entry.key, entry.value)
            seen_keys.add(entry.key)

    def _find_key_index(self, key: bytes) -> int:
        """Binary search for first entry with given key"""
        left, right = 0, len(self.entries)

        while left < right:
            mid = (left + right) // 2
            if self.entries[mid].key < key:
                left = mid + 1
            else:
                right = mid

        if left < len(self.entries) and self.entries[left].key == key:
            return left
        return -1

    def clear(self):
        """Clear all entries (after flush to disk)"""
        with self._lock:
            self.entries.clear()
            self.size_bytes = 0

    def snapshot(self) -> list:
        """Copy of all entries (all versions), for flushing."""
        with self._lock:
            return self.entries[:]

    def get_entry(self, key: bytes, timestamp: Optional[Timestamp] = None) -> Optional['MemTableEntry']:
        """Like get(), but returns the entry so callers can distinguish tombstones from misses."""
        with self._lock:
            idx = self._find_key_index(key)
            if idx == -1:
                return None
            for entry in self.entries[idx:]:
                if entry.key != key:
                    break
                if timestamp is None or entry.timestamp <= timestamp:
                    return entry
            return None

    def scan_entries(self, start_key: Optional[bytes] = None, end_key: Optional[bytes] = None) -> list:
        """All raw entries (all versions, including tombstones) in a key range."""
        with self._lock:
            return [e for e in self.entries
                    if (not start_key or e.key >= start_key) and (not end_key or e.key < end_key)]

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def is_full(self) -> bool:
        return self.size_bytes >= self.max_size_bytes
