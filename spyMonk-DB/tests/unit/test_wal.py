import struct
import zlib
from pathlib import Path

import pytest

from spymonk_enterprise.storage.engine.wal import WriteAheadLog, WALEntry, WAL_MAGIC, WAL_VERSION
from spymonk_enterprise.time.hybrid_clock import Timestamp


def make_ts(i):
    return Timestamp(physical=1_000_000 + i, logical=i)


def test_round_trip(tmp_path):
    wal = WriteAheadLog(tmp_path)
    for i in range(5):
        wal.append(make_ts(i), f"k{i}".encode(), f"v{i}".encode())
    wal.close()

    wal2 = WriteAheadLog(tmp_path)
    entries = list(wal2.replay())
    wal2.close()
    assert [e.key for e in entries] == [f"k{i}".encode() for i in range(5)]
    assert entries[3].value == b"v3"
    assert entries[0].timestamp == make_ts(0)


def test_new_files_have_versioned_header(tmp_path):
    wal = WriteAheadLog(tmp_path)
    wal.append(make_ts(0), b"k", b"v")
    wal.close()
    wal_file = sorted(tmp_path.glob("wal-*.log"))[0]
    raw = wal_file.read_bytes()
    assert raw[:4] == WAL_MAGIC
    assert raw[4] == WAL_VERSION


def test_crc_is_full_crc32(tmp_path):
    entry = WALEntry(make_ts(0), b"key", b"value")
    raw = entry.serialize()
    total_len = struct.unpack("I", raw[:4])[0]
    checksum = struct.unpack("I", raw[4:8])[0]
    data = raw[8:8 + total_len]
    assert checksum == (zlib.crc32(data) & 0xFFFFFFFF)


def test_corruption_stops_replay(tmp_path):
    wal = WriteAheadLog(tmp_path)
    for i in range(4):
        wal.append(make_ts(i), f"k{i}".encode(), b"v")
    wal.close()

    wal_file = sorted(tmp_path.glob("wal-*.log"))[0]
    raw = bytearray(wal_file.read_bytes())
    # Flip a byte inside the third record's payload (past header + 2 records).
    rec_len = len(WALEntry(make_ts(0), b"k0", b"v").serialize())
    corrupt_at = 5 + 2 * rec_len + 12
    raw[corrupt_at] ^= 0xFF
    wal_file.write_bytes(bytes(raw))

    wal2 = WriteAheadLog(tmp_path)
    entries = list(wal2.replay())
    wal2.close()
    assert len(entries) == 2  # stops at first corrupt record


def test_legacy_headerless_file_replays_as_v0(tmp_path):
    # Simulate a pre-upgrade WAL: no header, 1-byte blake2b checksum.
    import hashlib
    entry = WALEntry(make_ts(0), b"legacy", b"old")
    ts = entry.timestamp
    data = (struct.pack("QQQ", ts.physical, ts.logical, ts.uncertainty_ns)
            + struct.pack("B", 0)
            + struct.pack("I", 6) + b"legacy"
            + struct.pack("I", 3) + b"old")
    legacy_checksum = struct.pack("I", hashlib.blake2b(data, digest_size=4).digest()[0])
    (tmp_path / "wal-00000001.log").write_bytes(struct.pack("I", len(data)) + legacy_checksum + data)

    wal = WriteAheadLog(tmp_path)
    entries = list(wal.replay())
    assert len(entries) == 1
    assert entries[0].key == b"legacy"
    # Opening on a legacy file must rotate to a fresh v1 file for new appends.
    assert wal.current_seq >= 2
    wal.close()


def test_torn_length_prefix_stops_all_replay(tmp_path):
    wal = WriteAheadLog(tmp_path)
    wal.append(make_ts(0), b"k0", b"v")
    wal.close()
    # Second WAL file with a valid record.
    wal2 = WriteAheadLog(tmp_path)
    wal2._rotate()
    wal2.append(make_ts(1), b"k1", b"v")
    wal2.close()
    # Tear the FIRST file: append 2 stray bytes (a torn length prefix).
    first = sorted(tmp_path.glob("wal-*.log"))[0]
    with open(first, "ab") as f:
        f.write(b"\x99\x00")
    wal3 = WriteAheadLog(tmp_path)
    entries = list(wal3.replay())
    wal3.close()
    assert [e.key for e in entries] == [b"k0"]  # stops before file 2


def test_checkpoint_deletes_flushed_segments(tmp_path):
    wal = WriteAheadLog(tmp_path)
    wal.append(make_ts(0), b"k", b"v")
    wal.checkpoint()
    wal.append(make_ts(1), b"k2", b"v")
    assert len(sorted(tmp_path.glob("wal-*.log"))) == 1   # old segment removed
    entries = list(wal.replay())
    assert [e.key for e in entries] == [b"k2"]
    wal.close()
