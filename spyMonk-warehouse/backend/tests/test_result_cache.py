import time

from result_cache import ResultCache, normalize_sql


def test_normalize_sql_ignores_case_comments_whitespace():
    a = normalize_sql("SELECT  *\nFROM sales -- trailing comment\nWHERE id = 1")
    b = normalize_sql("select * from sales where id = 1")
    assert a == b


def test_key_includes_table_versions():
    cache = ResultCache(8, 60)
    k1 = cache.make_key("select * from t", [("t", 1)])
    k2 = cache.make_key("select * from t", [("t", 2)])
    assert k1 != k2


def test_put_get_and_lru_eviction():
    cache = ResultCache(2, 60)
    for i in range(3):
        cache.put(f"k{i}", {"i": i})
    assert cache.get("k0") is None          # evicted (LRU)
    assert cache.get("k2") == {"i": 2}
    assert cache.stats()["entries"] == 2


def test_ttl_expiry():
    cache = ResultCache(8, ttl_seconds=0.05)
    cache.put("k", {"v": 1})
    assert cache.get("k") == {"v": 1}
    time.sleep(0.08)
    assert cache.get("k") is None


def test_stats_count_hits_and_misses():
    cache = ResultCache(8, 60)
    cache.put("k", {})
    cache.get("k")
    cache.get("nope")
    s = cache.stats()
    assert s["hits"] == 1 and s["misses"] == 1
