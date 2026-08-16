from query_engine import extract_tables, extract_predicates, select_partitions


# ---- table extraction ----

def test_simple_from():
    assert extract_tables("SELECT * FROM sales") == ["sales"]

def test_join_and_aliases():
    sql = "SELECT u.name FROM users u JOIN orders o ON u.id = o.user_id"
    assert extract_tables(sql) == ["users", "orders"]

def test_comma_join_and_left_join():
    assert extract_tables("SELECT * FROM a, b LEFT JOIN c ON b.x = c.x") == ["a", "b", "c"]

def test_subquery_in_from_and_where():
    sql = ("SELECT * FROM (SELECT id FROM inner_t) sub "
           "WHERE id IN (SELECT id FROM other_t)")
    assert extract_tables(sql) == ["inner_t", "other_t"]

def test_dedup_preserves_order():
    assert extract_tables("SELECT * FROM t JOIN t ON 1=1") == ["t"]

def test_cte_names_are_not_tables():
    sql = ("WITH recent AS (SELECT * FROM sales WHERE id > 5) "
           "SELECT * FROM recent JOIN users ON recent.uid = users.id")
    assert extract_tables(sql) == ["sales", "users"]

def test_multiple_ctes_excluded():
    sql = ("WITH a AS (SELECT * FROM t1), b AS (SELECT * FROM t2) "
           "SELECT * FROM a JOIN b ON a.x = b.x")
    assert extract_tables(sql) == ["t1", "t2"]


# ---- predicate extraction ----

def test_conjunctive_predicates():
    preds = extract_predicates("SELECT * FROM t WHERE id >= 10 AND name = 'bob' AND id < 20")
    assert preds == {"id": [(">=", 10), ("<", 20)], "name": [("=", "bob")]}

def test_top_level_or_disables_pruning():
    assert extract_predicates("SELECT * FROM t WHERE id = 1 OR id = 9") == {}

def test_in_list():
    preds = extract_predicates("SELECT * FROM t WHERE id IN (1, 2, 3)")
    assert preds == {"id": [("IN", [1, 2, 3])]}

def test_functions_and_unknowns_are_skipped_not_fatal():
    preds = extract_predicates("SELECT * FROM t WHERE UPPER(name) = 'X' AND id = 5")
    assert preds == {"id": [("=", 5)]}

def test_no_where():
    assert extract_predicates("SELECT * FROM t") == {}


# ---- partition selection ----

META = {
    "version": 1,
    "columns": ["id", "name"],
    "partitions": [
        {"idx": 0, "rows": 100, "zone_map": {"id": {"min": 0, "max": 99, "null_count": 0},
                                             "name": {"min": "a", "max": "f", "null_count": 0}}},
        {"idx": 1, "rows": 100, "zone_map": {"id": {"min": 100, "max": 199, "null_count": 0},
                                             "name": {"min": "g", "max": "p", "null_count": 0}}},
        {"idx": 2, "rows": 100, "zone_map": {"id": {"min": 200, "max": 299, "null_count": 0},
                                             "name": None}},   # unorderable column
    ],
}


def test_pruning_by_range():
    selected, total = select_partitions(META, {"id": [("<", 100)]})
    assert (selected, total) == ([0], 3)
    selected, _ = select_partitions(META, {"id": [(">=", 150)]})
    assert selected == [1, 2]
    selected, _ = select_partitions(META, {"id": [("=", 250)]})
    assert selected == [2]

def test_none_zone_map_never_prunes():
    selected, _ = select_partitions(META, {"name": [("=", "zzz")]})
    assert selected == [2]   # 0,1 provably can't match; 2 has no zone map -> load

def test_type_mismatch_never_prunes():
    selected, _ = select_partitions(META, {"id": [("=", "abc")]})
    assert selected == [0, 1, 2]

def test_empty_predicates_loads_all():
    assert select_partitions(META, {}) == ([0, 1, 2], 3)

def test_case_colliding_columns_never_pruned():
    meta = {
        "version": 1,
        "columns": ["id", "ID"],
        "partitions": [
            {"idx": 0, "rows": 10, "zone_map": {"id": {"min": 0, "max": 9, "null_count": 0},
                                                "ID": {"min": 100, "max": 199, "null_count": 0}}},
        ],
    }
    # Predicate on "id" is ambiguous between "id" and "ID": must never prune.
    selected, _ = select_partitions(meta, {"id": [("=", 150)]})
    assert selected == [0]

def test_pruned_equals_unpruned_results():
    """Property: pruning must never change which rows CAN match."""
    import operator
    ops = {"=": operator.eq, "<": operator.lt, "<=": operator.le,
           ">": operator.gt, ">=": operator.ge}
    rows = {0: range(0, 100), 1: range(100, 200), 2: range(200, 300)}
    for op, lit in [("=", 150), ("<", 42), (">=", 299), (">", 500), ("<=", 0)]:
        selected, _ = select_partitions(META, {"id": [(op, lit)]})
        matching = {idx for idx, ids in rows.items()
                    if any(ops[op](i, lit) for i in ids)}
        assert matching.issubset(set(selected)), f"pruning dropped matches for {op} {lit}"
