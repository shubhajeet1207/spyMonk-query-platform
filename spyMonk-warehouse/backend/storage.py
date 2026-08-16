"""
Snowflake-style micro-partition storage on top of the spyMonk-DB KV client.

Tables are stored as immutable columnar partitions
(part:<table>:<version>:<idx> -> msgpack {col: [values]}) plus a meta record
with per-partition zone maps (min/max/null_count per column). Every
upload/delete bumps a never-deleted version counter (tablever:<table>) —
result-cache keys embed versions, so invalidation is implicit.
"""

import json
import threading
from typing import Any, Dict, List, Optional

import msgpack
import pandas as pd

from config import settings

# Coarse lock serializing store_table/delete_table mutation critical sections.
# Single-process embedded warehouse + infrequent uploads/deletes make a
# module-wide lock proportionate; it guarantees bump_version + partition
# writes + meta write + old-version cleanup happen atomically per call.
_STORAGE_LOCK = threading.Lock()


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Datetimes -> ISO strings, NaN/NaT -> None, so values are msgpack/JSON-safe."""
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%dT%H:%M:%S")
    return df.astype(object).where(pd.notnull(df), None)


def compute_zone_map(values: List[Any]) -> Optional[Dict[str, Any]]:
    """min/max/null_count for orderable columns; None disables pruning for the column."""
    non_null = [v for v in values if v is not None]
    null_count = len(values) - len(non_null)
    if not non_null:
        return {"min": None, "max": None, "null_count": null_count}
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null):
        return {"min": min(non_null), "max": max(non_null), "null_count": null_count}
    if all(isinstance(v, str) for v in non_null):
        return {"min": min(non_null), "max": max(non_null), "null_count": null_count}
    return None


def _meta_key(table: str) -> bytes:
    return f"table:{table}:meta".encode()


def _part_key(table: str, version: int, idx: int) -> bytes:
    return f"part:{table}:{version}:{idx}".encode()


def _ver_key(table: str) -> bytes:
    return f"tablever:{table}".encode()


class TableStorage:
    def __init__(self, client):
        self.client = client

    # -- versioning ----------------------------------------------------------

    def bump_version(self, table: str) -> int:
        raw = self.client.get(_ver_key(table))
        version = (int(raw.decode()) if raw else 0) + 1
        self.client.put(_ver_key(table), str(version).encode())
        return version

    # -- write path ----------------------------------------------------------

    def store_table(self, table: str, df: pd.DataFrame,
                    source_format: Optional[str] = None) -> dict:
        with _STORAGE_LOCK:
            old_meta = self._read_meta(table)
            df = clean_dataframe(df)
            df.columns = [str(c) for c in df.columns]
            lowered = [c.lower() for c in df.columns]
            if len(set(lowered)) != len(lowered):
                dupes = sorted({c for c in lowered if lowered.count(c) > 1})
                raise ValueError(f"Column names that differ only by case are not supported: {dupes}")
            columns = list(df.columns)
            version = self.bump_version(table)

            partitions = []
            rows = len(df)
            size = settings.PARTITION_ROWS
            if size <= 0:
                raise ValueError("PARTITION_ROWS must be positive")
            for idx, start in enumerate(range(0, rows, size)):
                chunk = df.iloc[start:start + size]
                col_data = {col: chunk[col].tolist() for col in columns}
                self.client.put(_part_key(table, version, idx),
                                msgpack.packb(col_data, use_bin_type=True))
                partitions.append({
                    "idx": idx,
                    "rows": len(chunk),
                    "zone_map": {col: compute_zone_map(col_data[col]) for col in columns},
                })

            meta = {
                "name": table,
                "columns": columns,                  # frontend compat
                "record_count": rows,                # frontend compat
                "row_count": rows,
                "version": version,
                "uploaded_at": pd.Timestamp.now().isoformat(),
                "source_format": source_format,      # csv / json / xlsx
                "schema": {col: str(dtype) for col, dtype in df.dtypes.items()},
                "partitions": partitions,
            }
            self.client.put(_meta_key(table), json.dumps(meta).encode())
            self._delete_data(table, old_meta)       # old version's partitions/rows
            return meta

    # -- read paths -----------------------------------------------------------

    def load_partitions(self, table: str, meta: dict, indexes: List[int]) -> pd.DataFrame:
        columns = meta["columns"]
        frames = []
        for idx in sorted(indexes):
            blob = self.client.get(_part_key(table, meta["version"], idx))
            if blob is None:
                continue
            col_data = msgpack.unpackb(blob, raw=False)
            frames.append(pd.DataFrame({col: col_data[col] for col in columns}))
        if not frames:
            return pd.DataFrame(columns=columns)
        return clean_dataframe(pd.concat(frames, ignore_index=True))

    def load_legacy(self, table: str, meta: dict, max_fetch: int = 100_000,
                    cancel_check=None) -> pd.DataFrame:
        count = min(meta.get("record_count", 0), max_fetch)
        records = []
        for i in range(count):
            if cancel_check and i % 100 == 0:
                cancel_check()
            raw = self.client.get(f"data:{table}:{i}".encode())
            if raw:
                records.append(json.loads(raw.decode()))
        return clean_dataframe(pd.DataFrame(records, columns=meta.get("columns")))

    # -- delete ----------------------------------------------------------------

    def delete_table(self, table: str, meta: dict) -> int:
        with _STORAGE_LOCK:
            rows = meta.get("row_count", meta.get("record_count", 0))
            self._delete_data(table, meta)
            self.client.delete(_meta_key(table))
            self.bump_version(table)   # never reuse a version a cache key may hold
            return rows

    # -- internal ---------------------------------------------------------------

    def _read_meta(self, table: str) -> Optional[dict]:
        raw = self.client.get(_meta_key(table))
        return json.loads(raw.decode()) if raw else None

    def _delete_data(self, table: str, meta: Optional[dict]) -> None:
        if not meta:
            return
        if "partitions" in meta:
            for part in meta["partitions"]:
                self.client.delete(_part_key(table, meta["version"], part["idx"]))
        else:
            for i in range(meta.get("record_count", 0)):
                self.client.delete(f"data:{table}:{i}".encode())
