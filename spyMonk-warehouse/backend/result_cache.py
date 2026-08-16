"""
Snowflake-style result cache: keyed on normalized SQL + the version of every
table the query touches. Uploads and deletes bump table versions, so stale
entries can never be served — they simply stop being addressed.
"""

import hashlib
import threading
import time
from collections import OrderedDict
from typing import List, Optional, Tuple

import sqlparse


def normalize_sql(sql: str) -> str:
    formatted = sqlparse.format(sql, strip_comments=True, keyword_case="lower")
    return " ".join(formatted.split())


class ResultCache:
    def __init__(self, max_entries: int, ttl_seconds: float):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict = OrderedDict()   # key -> (expires_at, payload)
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def make_key(self, normalized_sql: str, table_versions: List[Tuple[str, int]]) -> str:
        version_part = ";".join(f"{t}:{v}" for t, v in sorted(table_versions))
        return hashlib.sha256(f"{normalized_sql}|{version_part}".encode()).hexdigest()

    def get(self, key: str) -> Optional[dict]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            expires_at, payload = entry
            if time.monotonic() > expires_at:
                del self._entries[key]
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return payload

    def put(self, key: str, payload: dict) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic() + self.ttl_seconds, payload)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict:
        with self._lock:
            return {"hits": self._hits, "misses": self._misses,
                    "entries": len(self._entries)}
