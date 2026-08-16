import io

import pytest
from fastapi.testclient import TestClient

import main
from config import settings
from tests.conftest import FakeKV

HEADERS = {"X-API-Key": "test-key"}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(main, "client", FakeKV())
    monkeypatch.setattr(settings, "ALLOWED_API_KEYS_RAW", "test-key")
    monkeypatch.setattr(settings, "PARTITION_ROWS", 100)
    main.result_cache.clear()
    main.limiter.reset()   # in-memory rate limits accumulate across tests
    return TestClient(main.app)


def upload_csv(api, filename, csv_text):
    return api.post(
        "/upload", headers=HEADERS,
        files={"file": (filename, io.BytesIO(csv_text.encode()), "text/csv")})


def test_upload_join_and_cache_flow(api):
    users = "id,name\n1,alice\n2,bob\n"
    orders = "order_id,user_id,amount\n10,1,50\n11,1,25\n12,2,10\n"
    assert upload_csv(api, "users.csv", users).status_code == 200
    r = upload_csv(api, "orders.csv", orders)
    assert r.status_code == 200 and r.json()["version"] == 1

    join_q = ("SELECT u.name, SUM(o.amount) AS total FROM users u "
              "JOIN orders o ON u.id = o.user_id GROUP BY u.name ORDER BY u.name")

    r1 = api.post("/query", headers=HEADERS, json={"query": join_q}).json()
    assert r1["success"] is True
    assert r1["cache_hit"] is False
    assert r1["tables_used"] == ["users", "orders"]
    assert r1["results"] == [{"name": "alice", "total": 75}, {"name": "bob", "total": 10}]

    r2 = api.post("/query", headers=HEADERS, json={"query": join_q}).json()
    assert r2["cache_hit"] is True
    assert r2["results"] == r1["results"]

    # Re-upload bumps the version -> cache key changes -> fresh execution
    upload_csv(api, "orders.csv", "order_id,user_id,amount\n13,2,99\n")
    r3 = api.post("/query", headers=HEADERS, json={"query": join_q}).json()
    assert r3["cache_hit"] is False
    assert r3["results"] == [{"name": "bob", "total": 99}]


def test_partition_pruning_stats(api):
    rows = "\n".join(f"{i},item-{i}" for i in range(300))
    upload_csv(api, "big.csv", "id,label\n" + rows)

    r = api.post("/query", headers=HEADERS,
                 json={"query": "SELECT * FROM big WHERE id < 50"}).json()
    assert r["success"] is True
    assert r["partitions_total"] == 3
    assert r["partitions_scanned"] == 1
    assert r["row_count"] == 50

    r_all = api.post("/query", headers=HEADERS,
                     json={"query": "SELECT COUNT(*) AS n FROM big"}).json()
    assert r_all["partitions_scanned"] == 3
    assert r_all["results"] == [{"n": 300}]


def test_missing_table_404_names_the_table(api):
    upload_csv(api, "users.csv", "id,name\n1,a\n")
    r = api.post("/query", headers=HEADERS,
                 json={"query": "SELECT * FROM users JOIN ghosts ON 1=1"})
    assert r.status_code == 404
    assert "ghosts" in r.json()["detail"]


def test_delete_invalidates_cache_via_version(api):
    upload_csv(api, "t.csv", "id\n1\n")
    q = {"query": "SELECT * FROM t"}
    api.post("/query", headers=HEADERS, json=q)
    assert api.delete("/tables/t", headers=HEADERS).status_code == 200
    r = api.post("/query", headers=HEADERS, json=q)
    assert r.status_code == 404   # table gone; cached result not served


def test_delete_then_reupload_never_serves_stale_cache(api):
    """User scenario: cache a result, delete the table, re-upload different
    data under the same name — the same query must return the NEW data."""
    upload_csv(api, "t.csv", "id,val\n1,old\n")
    q = {"query": "SELECT val FROM t"}

    r1 = api.post("/query", headers=HEADERS, json=q).json()
    assert r1["results"] == [{"val": "old"}]
    r2 = api.post("/query", headers=HEADERS, json=q).json()
    assert r2["cache_hit"] is True          # old result is now cached

    assert api.delete("/tables/t", headers=HEADERS).status_code == 200
    upload_csv(api, "t.csv", "id,val\n1,new\n")

    r3 = api.post("/query", headers=HEADERS, json=q).json()
    assert r3["cache_hit"] is False         # version bumped -> new cache key
    assert r3["results"] == [{"val": "new"}]


def test_use_cache_false_bypasses(api):
    upload_csv(api, "t.csv", "id\n1\n")
    q = "SELECT * FROM t"
    api.post("/query", headers=HEADERS, json={"query": q})
    r = api.post("/query", headers=HEADERS, json={"query": q, "use_cache": False}).json()
    assert r["cache_hit"] is False


def test_legacy_row_layout_still_queryable(api):
    # Simulate a table written by the pre-partition backend
    import json as _json
    kv = main.client
    for i in range(5):
        kv.put(f"data:old:{i}".encode(), _json.dumps({"id": i, "v": f"r{i}"}).encode())
    kv.put(b"table:old:meta", _json.dumps(
        {"name": "old", "columns": ["id", "v"], "record_count": 5}).encode())

    r = api.post("/query", headers=HEADERS,
                 json={"query": "SELECT COUNT(*) AS n FROM old"}).json()
    assert r["success"] is True
    assert r["results"] == [{"n": 5}]


def test_upload_rejects_case_colliding_columns(api):
    r = upload_csv(api, "bad.csv", "id,ID\n1,2\n")
    assert r.status_code == 400
    assert "case" in r.json()["detail"].lower()


def test_unauthenticated_request_rejected(api):
    r = api.post("/query", json={"query": "SELECT 1"})
    assert r.status_code == 401


def test_non_select_rejected(api):
    upload_csv(api, "t.csv", "id\n1\n")
    r = api.post("/query", headers=HEADERS, json={"query": "DROP TABLE t"})
    assert r.status_code == 400


def test_table_details_definitions_format_and_history(api):
    upload_csv(api, "users.csv", "id,name\n1,alice\n2,bob\n")

    # Six distinct queries: history must keep only the most recent five.
    queries = [f"SELECT * FROM users LIMIT {i}" for i in range(1, 7)]
    for q in queries:
        assert api.post("/query", headers=HEADERS, json={"query": q}).json()["success"]

    r = api.get("/tables/users", headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "users"
    assert d["column_count"] == 2 and d["columns"] == ["id", "name"]
    assert d["record_count"] == 2
    assert d["source_format"] == "csv"
    assert set(d["schema"].keys()) == {"id", "name"}   # table definitions
    hist = d["last_queries"]
    assert len(hist) == 5
    assert hist[0]["query"] == queries[-1]              # most recent first
    assert hist[-1]["query"] == queries[1]              # oldest (q1) evicted
    assert all("at" in h for h in hist)


def test_history_records_cache_hits_and_joins(api):
    upload_csv(api, "users.csv", "id,name\n1,a\n")
    upload_csv(api, "orders.csv", "oid,user_id\n7,1\n")
    join_q = "SELECT * FROM users u JOIN orders o ON u.id = o.user_id"
    api.post("/query", headers=HEADERS, json={"query": join_q})
    api.post("/query", headers=HEADERS, json={"query": join_q})   # cache hit

    for t in ("users", "orders"):                        # recorded on BOTH tables
        hist = api.get(f"/tables/{t}", headers=HEADERS).json()["last_queries"]
        assert len(hist) == 2
        assert hist[0]["cache_hit"] is True and hist[1]["cache_hit"] is False


def test_table_details_404(api):
    r = api.get("/tables/ghost", headers=HEADERS)
    assert r.status_code == 404


def test_table_data_endpoint_returns_all_rows(api):
    upload_csv(api, "big.csv", "id,label\n" + "\n".join(f"{i},x{i}" for i in range(250)))
    r = api.get("/tables/big/data", headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["columns"] == ["id", "label"]
    assert d["row_count"] == 250 and len(d["results"]) == 250
    assert d["results"][0] == {"id": 0, "label": "x0"}
    assert d["source_format"] == "csv"


def test_table_data_legacy_layout(api):
    import json as _json
    kv = main.client
    for i in range(3):
        kv.put(f"data:old:{i}".encode(), _json.dumps({"id": i}).encode())
    kv.put(b"table:old:meta", _json.dumps(
        {"name": "old", "columns": ["id"], "record_count": 3}).encode())
    d = api.get("/tables/old/data", headers=HEADERS).json()
    assert d["row_count"] == 3 and d["results"][2] == {"id": 2}


def test_viewing_data_does_not_pollute_history(api):
    upload_csv(api, "t.csv", "id\n1\n")
    api.get("/tables/t/data", headers=HEADERS)
    api.get("/tables/t", headers=HEADERS)
    hist = api.get("/tables/t", headers=HEADERS).json()["last_queries"]
    assert hist == []


def test_delete_clears_query_history(api):
    upload_csv(api, "t.csv", "id\n1\n")
    api.post("/query", headers=HEADERS, json={"query": "SELECT * FROM t"})
    assert api.delete("/tables/t", headers=HEADERS).status_code == 200
    upload_csv(api, "t.csv", "id\n9\n")                  # same name, fresh table
    hist = api.get("/tables/t", headers=HEADERS).json()["last_queries"]
    assert hist == []                                    # old history not inherited


def test_cors_preflight_allows_delete(api):
    """Browsers preflight cross-origin DELETEs with OPTIONS; the UI's delete
    button is dead if the CORS middleware doesn't allow the DELETE method."""
    r = api.options("/tables/anything", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "DELETE",
        "Access-Control-Request-Headers": "x-api-key",
    })
    assert r.status_code == 200
    assert "DELETE" in r.headers.get("access-control-allow-methods", "")
