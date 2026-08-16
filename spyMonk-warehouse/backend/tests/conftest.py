import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/ on path

import pytest


class FakeTxn:
    def __init__(self, kv):
        self.kv = kv

    def put(self, k, v): self.kv.data[k] = v
    def get(self, k): return self.kv.data.get(k)
    def delete(self, k): self.kv.data.pop(k, None)
    def commit(self): return True
    def abort(self): pass


class FakeKV:
    """Duck-type of SpyMonkClient's KV surface, dict-backed."""

    def __init__(self):
        self.data = {}

    def put(self, k, v): self.data[k] = v
    def get(self, k): return self.data.get(k)
    def delete(self, k): self.data.pop(k, None)

    def scan(self, start=None, end=None):
        for k in sorted(self.data):
            if (start is None or k >= start) and (end is None or k < end):
                yield k, self.data[k]

    def begin_transaction(self, read_only=False):
        return FakeTxn(self)


@pytest.fixture
def kv():
    return FakeKV()
