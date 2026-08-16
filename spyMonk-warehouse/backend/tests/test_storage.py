import json

import pandas as pd
import pytest

from config import settings
from storage import TableStorage, compute_zone_map, clean_dataframe


@pytest.fixture(autouse=True)
def small_partitions(monkeypatch):
    monkeypatch.setattr(settings, "PARTITION_ROWS", 100)


def make_df(n=250):
    return pd.DataFrame({
        "id": range(n),
        "name": [f"user-{i}" for i in range(n)],
        "amount": [i * 1.5 for i in range(n)],
    })


def test_store_creates_partitions_and_zone_maps(kv):
    storage = TableStorage(kv)
    meta = storage.store_table("sales", make_df(250))
    assert meta["version"] == 1
    assert meta["row_count"] == 250
    assert [p["rows"] for p in meta["partitions"]] == [100, 100, 50]
    zm0 = meta["partitions"][0]["zone_map"]
    assert zm0["id"] == {"min": 0, "max": 99, "null_count": 0}
    assert zm0["name"]["min"] == "user-0"
    stored_meta = json.loads(kv.get(b"table:sales:meta").decode())
    assert stored_meta["version"] == 1


def test_load_partitions_round_trip(kv):
    storage = TableStorage(kv)
    meta = storage.store_table("sales", make_df(250))
    df = storage.load_partitions("sales", meta, [0, 2])
    assert len(df) == 150
    assert list(df.columns) == ["id", "name", "amount"]
    assert df["id"].iloc[0] == 0 and df["id"].iloc[-1] == 249


def test_reupload_bumps_version_and_removes_old_partitions(kv):
    storage = TableStorage(kv)
    storage.store_table("sales", make_df(250))
    meta2 = storage.store_table("sales", make_df(50))
    assert meta2["version"] == 2
    assert not [k for k in kv.data if k.startswith(b"part:sales:1:")]
    assert [k for k in kv.data if k.startswith(b"part:sales:2:")]


def test_delete_preserves_version_counter(kv):
    storage = TableStorage(kv)
    storage.store_table("sales", make_df(50))
    meta = json.loads(kv.get(b"table:sales:meta").decode())
    storage.delete_table("sales", meta)
    assert kv.get(b"table:sales:meta") is None
    assert not [k for k in kv.data if k.startswith(b"part:sales:")]
    meta3 = storage.store_table("sales", make_df(10))
    assert meta3["version"] == 3  # 1=first upload, 2=delete bump, 3=re-upload


def test_zone_map_mixed_types_is_none():
    assert compute_zone_map([1, "a", None]) is None
    assert compute_zone_map([True, False]) is None          # bools: not orderable-pruned
    assert compute_zone_map([None, None]) == {"min": None, "max": None, "null_count": 2}


def test_clean_dataframe_handles_datetimes_and_nan():
    df = pd.DataFrame({"ts": pd.to_datetime(["2024-01-01", None]), "x": [1.0, float("nan")]})
    out = clean_dataframe(df)
    assert out["ts"].iloc[0] == "2024-01-01T00:00:00"
    assert out["ts"].iloc[1] is None
    assert out["x"].iloc[1] is None


def test_legacy_fallback_loads_row_layout(kv):
    records = [{"id": i, "v": f"r{i}"} for i in range(5)]
    for i, rec in enumerate(records):
        kv.put(f"data:old:{i}".encode(), json.dumps(rec).encode())
    meta = {"name": "old", "columns": ["id", "v"], "record_count": 5}
    df = TableStorage(kv).load_legacy("old", meta)
    assert len(df) == 5 and df["v"].iloc[4] == "r4"


def test_case_colliding_columns_rejected(kv):
    df = pd.DataFrame({"id": [1], "ID": [2]})
    with pytest.raises(ValueError, match="differ only by case"):
        TableStorage(kv).store_table("t", df)


def test_non_string_columns_are_stringified(kv):
    df = pd.DataFrame({0: [1, 2], "b": ["x", "y"]})
    meta = TableStorage(kv).store_table("t", df)
    assert meta["columns"] == ["0", "b"]


def test_store_table_records_source_format(kv):
    meta = TableStorage(kv).store_table("t", make_df(10), source_format="json")
    assert meta["source_format"] == "json"
    # omitted -> stored as None, key always present
    meta2 = TableStorage(kv).store_table("u", make_df(10))
    assert meta2["source_format"] is None
