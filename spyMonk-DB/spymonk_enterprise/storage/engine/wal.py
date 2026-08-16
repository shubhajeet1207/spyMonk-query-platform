"""
Write-Ahead Log (WAL) for durability.

Before any write is applied to the MemTable, it's written to the WAL.
This ensures durability: if the server crashes, we can replay the WAL
to recover all committed writes.

WAL Format:
- File starts with 5-byte header: b'SWAL' (4 bytes) + 1 version byte
- Each record: [length][crc32][timestamp][deleted][key_len][key][value_len][value]
- Legacy version 0 (headerless, 1-byte blake2b checksum) is read-only; appends rotate to v1
- Fsync after each write (for durability)
"""

import os
import struct
import hashlib
import zlib
import threading
from pathlib import Path
from typing import Optional, Iterator, Tuple
import logging

from spymonk_enterprise.time.hybrid_clock import Timestamp

logger = logging.getLogger(__name__)

WAL_MAGIC = b'SWAL'
WAL_VERSION = 1
_HEADER_LEN = 5


class WALEntry:
    """Single WAL entry"""

    __slots__ = ('timestamp', 'key', 'value', 'deleted')

    def __init__(self, timestamp: Timestamp, key: bytes, value: Optional[bytes], deleted: bool = False):
        self.timestamp = timestamp
        self.key = key
        self.value = value
        self.deleted = deleted

    def serialize(self) -> bytes:
        """Serialize entry to bytes"""
        # Timestamp (3 * 8 bytes)
        ts_bytes = struct.pack('QQQ', self.timestamp.physical, self.timestamp.logical, self.timestamp.uncertainty_ns)

        # Deleted flag (1 byte)
        deleted_byte = struct.pack('B', 1 if self.deleted else 0)

        # Key
        key_len = struct.pack('I', len(self.key))
        key_bytes = self.key

        # Value (None for deletes)
        if self.value is None:
            value_len = struct.pack('I', 0)
            value_bytes = b''
        else:
            value_len = struct.pack('I', len(self.value))
            value_bytes = self.value

        # Combine
        data = ts_bytes + deleted_byte + key_len + key_bytes + value_len + value_bytes

        # Checksum (CRC32)
        checksum = struct.pack('I', zlib.crc32(data) & 0xFFFFFFFF)

        # Total length
        total_len = struct.pack('I', len(data))

        return total_len + checksum + data

    @classmethod
    def deserialize(cls, data: bytes) -> 'WALEntry':
        """Deserialize entry from bytes"""
        offset = 0

        # Timestamp
        physical, logical, uncertainty = struct.unpack('QQQ', data[offset:offset + 24])
        offset += 24
        timestamp = Timestamp(physical, logical, uncertainty)

        # Deleted flag
        deleted = struct.unpack('B', data[offset:offset + 1])[0] == 1
        offset += 1

        # Key
        key_len = struct.unpack('I', data[offset:offset + 4])[0]
        offset += 4
        key = data[offset:offset + key_len]
        offset += key_len

        # Value
        value_len = struct.unpack('I', data[offset:offset + 4])[0]
        offset += 4
        if value_len > 0:
            value = data[offset:offset + value_len]
        else:
            value = None

        return cls(timestamp, key, value, deleted)


class WriteAheadLog:
    """
    Write-Ahead Log for durability.

    All writes go through the WAL before being applied to MemTable.
    On restart, we replay the WAL to recover uncommitted data.
    """

    def __init__(self, wal_dir: Path, max_size_bytes: int = 64 * 1024 * 1024):
        """
        Initialize WAL.

        Args:
            wal_dir: Directory to store WAL files
            max_size_bytes: Maximum WAL file size before rotation (default: 64MB)
        """
        self.wal_dir = Path(wal_dir)
        self.max_size_bytes = max_size_bytes

        # Create WAL directory
        self.wal_dir.mkdir(parents=True, exist_ok=True)

        # Current WAL file
        self.current_file = None
        self.current_seq = 0
        self.current_size = 0

        # Thread safety
        self._lock = threading.Lock()

        # Initialize
        self._find_latest_wal()
        self._open_wal()

        logger.info(f"Initialized WAL in {wal_dir}, sequence={self.current_seq}")

    def append(self, timestamp: Timestamp, key: bytes, value: Optional[bytes], deleted: bool = False):
        """
        Append entry to WAL.

        This is synchronous and includes fsync for durability.
        """
        entry = WALEntry(timestamp, key, value, deleted)
        serialized = entry.serialize()

        with self._lock:
            # Check if we need to rotate
            if self.current_size + len(serialized) > self.max_size_bytes:
                self._rotate()

            # Write to file
            self.current_file.write(serialized)
            self.current_file.flush()
            os.fsync(self.current_file.fileno())

            self.current_size += len(serialized)

    def replay(self) -> Iterator[WALEntry]:
        """Replay all WAL files. Stops at the first corrupt record."""
        wal_files = sorted(self.wal_dir.glob('wal-*.log'))
        recovered = 0

        for wal_file in wal_files:
            logger.info(f"Replaying WAL file: {wal_file}")
            with open(wal_file, 'rb') as f:
                header = f.read(_HEADER_LEN)
                if header[:4] == WAL_MAGIC:
                    version = header[4]
                    if version > WAL_VERSION:
                        logger.error(f"Unknown WAL version {version} in {wal_file}; stopping replay")
                        return
                else:
                    version = 0
                    f.seek(0)

                while True:
                    length_bytes = f.read(4)
                    if not length_bytes:
                        break
                    if len(length_bytes) < 4:
                        logger.error(f"Truncated WAL record in {wal_file}; stopping replay ({recovered} recovered)")
                        return
                    total_len = struct.unpack('I', length_bytes)[0]
                    checksum_bytes = f.read(4)
                    data = f.read(total_len)
                    if len(checksum_bytes) < 4 or len(data) < total_len:
                        logger.error(f"Truncated WAL record in {wal_file}; stopping replay ({recovered} recovered)")
                        return
                    expected = struct.unpack('I', checksum_bytes)[0]
                    if version == 0:
                        actual = hashlib.blake2b(data, digest_size=4).digest()[0]
                    else:
                        actual = zlib.crc32(data) & 0xFFFFFFFF
                    if expected != actual:
                        logger.error(f"WAL corruption in {wal_file}; stopping replay ({recovered} recovered)")
                        return
                    recovered += 1
                    yield WALEntry.deserialize(data)

        logger.info(f"WAL replay complete: {recovered} records")

    def checkpoint(self):
        """Rotate, then delete fully-flushed segments (everything older than the new file)."""
        with self._lock:
            self._rotate()
            for wal_file in sorted(self.wal_dir.glob('wal-*.log')):
                try:
                    seq = int(wal_file.stem.split('-')[1])
                except (IndexError, ValueError):
                    continue
                if seq < self.current_seq:
                    wal_file.unlink(missing_ok=True)

    def _rotate(self):
        """Rotate to a new WAL file"""
        if self.current_file:
            self.current_file.close()

        self.current_seq += 1
        wal_path = self.wal_dir / f"wal-{self.current_seq:08d}.log"

        self.current_file = open(wal_path, 'wb')
        self.current_file.write(WAL_MAGIC + bytes([WAL_VERSION]))
        self.current_file.flush()
        self.current_size = _HEADER_LEN

        logger.info(f"Rotated to new WAL file: {wal_path}")

    def _find_latest_wal(self):
        """Find the latest WAL sequence number"""
        wal_files = list(self.wal_dir.glob('wal-*.log'))

        if not wal_files:
            self.current_seq = 0
            return

        # Extract sequence numbers
        sequences = []
        for wal_file in wal_files:
            try:
                seq = int(wal_file.stem.split('-')[1])
                sequences.append(seq)
            except (IndexError, ValueError):
                continue

        self.current_seq = max(sequences) if sequences else 0

    def _open_wal(self):
        """Open the current WAL file for appending"""
        wal_path = self.wal_dir / f"wal-{self.current_seq:08d}.log"

        if wal_path.exists():
            with open(wal_path, 'rb') as f:
                header = f.read(_HEADER_LEN)
            if header[:4] != WAL_MAGIC:
                # Legacy (version-0) file: never append to it; rotate to a v1 file.
                self.current_file = None
                self._rotate()
                return
            self.current_file = open(wal_path, 'ab')
            self.current_size = wal_path.stat().st_size
        else:
            self.current_file = open(wal_path, 'wb')
            self.current_file.write(WAL_MAGIC + bytes([WAL_VERSION]))
            self.current_file.flush()
            self.current_size = _HEADER_LEN

    def close(self):
        """Close the WAL"""
        if self.current_file:
            self.current_file.close()
            self.current_file = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
