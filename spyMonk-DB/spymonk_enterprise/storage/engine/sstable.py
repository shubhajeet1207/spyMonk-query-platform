"""
SSTable: immutable sorted on-disk table.

Layout:
    [4B magic 'SSTB'][1B version]
    [msgpack records: [key, physical, logical, uncertainty, deleted, value]] * N
    [msgpack index: list of [key, file_offset]]  (sparse, every INDEX_EVERY records)
    [footer: Q index_offset, Q entry_count, I crc32-of-everything-before-crc]
"""

import io
import os
import struct
import threading
import zlib
from pathlib import Path
from typing import Iterable, Iterator, Optional, Tuple
import bisect

import msgpack

from spymonk_enterprise.time.hybrid_clock import Timestamp

MAGIC = b'SSTB'
VERSION = 1
INDEX_EVERY = 64
_FOOTER = struct.Struct('<QQI')


class SSTableCorruption(Exception):
    """SSTable failed integrity verification."""


def write_sstable(path: Path, entries: Iterable[Tuple[bytes, Timestamp, bool, Optional[bytes]]]) -> None:
    path = Path(path)
    ordered = sorted(entries, key=lambda e: (e[0], -e[1].physical, -e[1].logical))

    tmp = path.with_suffix(path.suffix + '.tmp')
    buf = bytearray()
    buf += MAGIC + bytes([VERSION])

    index = []
    for i, (key, ts, deleted, value) in enumerate(ordered):
        if i % INDEX_EVERY == 0:
            index.append([key, len(buf)])
        buf += msgpack.packb([key, ts.physical, ts.logical, ts.uncertainty_ns, deleted, value])

    index_offset = len(buf)
    buf += msgpack.packb(index)
    buf += struct.pack('<QQ', index_offset, len(ordered))
    buf += struct.pack('<I', zlib.crc32(bytes(buf)) & 0xFFFFFFFF)

    with open(tmp, 'wb') as f:
        f.write(buf)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    # Fsync the parent directory so the rename itself is durable before any
    # WAL truncation that depends on this SSTable existing can follow.
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class _PreadStream(io.RawIOBase):
    """Read-only file-like view over an existing fd, using os.pread.

    Every instance keeps its own position and never touches the fd's shared
    offset, so any number of concurrent read operations on one SSTableReader
    are safe. (An os.dup'd fd would NOT work here: dup'd descriptors share one
    open file description and therefore one seek position.) pread also keeps
    working after the file's path is unlinked, which in-flight reads on
    compacted-away (refcounted) readers rely on.
    """

    def __init__(self, fd: int, pos: int):
        super().__init__()
        self._fd = fd
        self._pos = pos

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        data = os.pread(self._fd, len(b), self._pos)
        n = len(data)
        b[:n] = data
        self._pos += n
        return n


class SSTableReader:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._ref_lock = threading.Lock()
        self._refs = 1        # owner (the store) holds one reference
        self._closed = False
        self._f = open(self.path, 'rb')
        raw = self._f.read()
        if len(raw) < 5 + _FOOTER.size or raw[:4] != MAGIC:
            self._f.close()
            raise SSTableCorruption(f"{path}: bad magic or truncated")
        index_offset, self.entry_count = struct.unpack('<QQ', raw[-20:-4])
        crc = struct.unpack('<I', raw[-4:])[0]
        if zlib.crc32(raw[:-4]) & 0xFFFFFFFF != crc:
            self._f.close()
            raise SSTableCorruption(f"{path}: CRC mismatch")
        if not (5 <= index_offset <= len(raw) - _FOOTER.size):
            self._f.close()
            raise SSTableCorruption(f"{path}: index_offset out of range")
        self._data_end = index_offset
        # Sparse index: parallel arrays for bisect.
        try:
            index = msgpack.unpackb(raw[index_offset:-_FOOTER.size], raw=True)
        except Exception as e:
            self._f.close()
            raise SSTableCorruption(f"{path}: index unpack failed: {e}") from e
        self._index_keys = [e[0] for e in index]
        self._index_offsets = [e[1] for e in index]

    def _records_from(self, offset: int) -> Iterator[Tuple[bytes, Timestamp, bool, Optional[bytes]]]:
        # Give each read operation its own stream with an independent file
        # position; concurrent get()/scan() on one reader must not share seek
        # state. _PreadStream never touches the fd's shared offset and stays
        # valid after the file is unlinked (compacted-away readers).
        f = io.BufferedReader(_PreadStream(self._f.fileno(), offset))
        try:
            unpacker = msgpack.Unpacker(f, raw=True)
            pos = offset
            while pos < self._data_end:
                key, phys, logical, unc, deleted, value = unpacker.unpack()
                pos = offset + unpacker.tell()
                yield key, Timestamp(physical=phys, logical=logical, uncertainty_ns=unc), bool(deleted), value
        finally:
            f.close()

    def _seek_offset(self, key: bytes) -> int:
        if not self._index_keys:
            return 5
        i = bisect.bisect_left(self._index_keys, key) - 1
        return self._index_offsets[max(i, 0)]

    def get(self, key: bytes, read_ts: Timestamp) -> Optional[Tuple[Optional[bytes], bool]]:
        for rkey, rts, deleted, value in self._records_from(self._seek_offset(key)):
            if rkey > key:
                return None
            if rkey == key and rts <= read_ts:
                return (value, deleted)
        return None

    def scan(self, start: Optional[bytes] = None, end: Optional[bytes] = None
             ) -> Iterator[Tuple[bytes, Timestamp, bool, Optional[bytes]]]:
        offset = self._seek_offset(start) if start else 5
        for rec in self._records_from(offset):
            if start and rec[0] < start:
                continue
            if end and rec[0] >= end:
                return
            yield rec

    def acquire(self) -> bool:
        """Take a read reference. False if already fully closed (caller must retry with a fresh reader list)."""
        with self._ref_lock:
            if self._closed:
                return False
            self._refs += 1
            return True

    def release(self) -> None:
        with self._ref_lock:
            self._refs -= 1
            if self._refs <= 0 and not self._closed:
                self._closed = True
                self._f.close()

    def close(self) -> None:
        """Drop the owner reference; the file closes once the last in-flight read releases."""
        self.release()
