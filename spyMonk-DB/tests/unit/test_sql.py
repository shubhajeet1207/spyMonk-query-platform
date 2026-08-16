# tests/unit/test_sql.py
"""Unit tests for the SQL layer: parser, WHERE predicates, and executor.

Executor tests run against a real MVCCStore + SchemaRegistry (no mocks),
mirroring how tests/unit/test_mvcc_flush.py constructs its store.
"""

import pytest

from spymonk_enterprise.schema.schema import ColumnType, SchemaError, SchemaRegistry
from spymonk_enterprise.sql.executor.executor import QueryExecutor, QueryResult
from spymonk_enterprise.sql.parser.ast import (
    CreateTableStatement, DeleteStatement, InsertStatement, SelectStatement,
    UpdateStatement)
from spymonk_enterprise.sql.parser.predicates import (
    Comparison, Logical, Not, PredicateError, evaluate, parse_literal_text,
    parse_predicate)
from spymonk_enterprise.sql.parser.sql_parser import SQLParser
from spymonk_enterprise.storage.mvcc import MVCCStore
from spymonk_enterprise.time.hybrid_clock import HybridLogicalClock


@pytest.fixture
def parser():
    return SQLParser()


# ---------------------------------------------------------------------------
# Parser: SELECT
# ---------------------------------------------------------------------------

def test_select_star(parser):
    stmt = parser.parse("SELECT * FROM Users")
    assert isinstance(stmt, SelectStatement)
    assert stmt.columns == ["*"]
    assert stmt.from_table == "Users"
    assert stmt.where_clause is None
    assert stmt.order_by is None
    assert stmt.limit is None
    assert stmt.offset is None
    assert stmt.is_read_only() is True


def test_select_column_list(parser):
    stmt = parser.parse("SELECT name, email FROM Users")
    assert stmt.columns == ["name", "email"]


def test_select_single_column(parser):
    stmt = parser.parse("SELECT name FROM Users")
    assert stmt.columns == ["name"]


def test_select_where_single_predicate(parser):
    stmt = parser.parse("SELECT * FROM Users WHERE age > 21")
    assert stmt.where_clause == Comparison(column="age", op=">", literal=21)


def test_select_where_and(parser):
    stmt = parser.parse("SELECT * FROM Users WHERE age > 21 AND city = 'NYC'")
    assert stmt.where_clause == Logical("AND", [
        Comparison(column="age", op=">", literal=21),
        Comparison(column="city", op="=", literal="NYC"),
    ])


def test_select_where_or(parser):
    stmt = parser.parse("SELECT * FROM Users WHERE age < 18 OR age > 65")
    assert stmt.where_clause == Logical("OR", [
        Comparison(column="age", op="<", literal=18),
        Comparison(column="age", op=">", literal=65),
    ])


def test_select_limit_and_offset(parser):
    stmt = parser.parse("SELECT * FROM Users LIMIT 10 OFFSET 5")
    assert stmt.limit == 10
    assert stmt.offset == 5


def test_select_order_by(parser):
    stmt = parser.parse("SELECT * FROM Users ORDER BY age DESC, name")
    assert stmt.order_by == [("age", "DESC"), ("name", "ASC")]


def test_select_where_order_limit_combo(parser):
    stmt = parser.parse("SELECT * FROM Users WHERE age > 21 ORDER BY name LIMIT 3")
    assert stmt.where_clause == Comparison(column="age", op=">", literal=21)
    assert stmt.order_by == [("name", "ASC")]
    assert stmt.limit == 3


def test_select_missing_from_raises(parser):
    with pytest.raises(ValueError, match="missing FROM"):
        parser.parse("SELECT *")


# ---------------------------------------------------------------------------
# Parser: INSERT
# ---------------------------------------------------------------------------

def test_insert_single_row(parser):
    stmt = parser.parse("INSERT INTO Users (user_id, name) VALUES (1, 'Alice')")
    assert isinstance(stmt, InsertStatement)
    assert stmt.table == "Users"
    assert stmt.columns == ["user_id", "name"]
    assert stmt.values == [[1, "Alice"]]
    assert stmt.is_read_only() is False


def test_insert_literal_typing(parser):
    stmt = parser.parse(
        "INSERT INTO T (a, b, c, d, e) VALUES (1, 1.5, 'x', TRUE, NULL)")
    assert stmt.values == [[1, 1.5, "x", True, None]]


def test_insert_value_with_comma_inside_quotes(parser):
    stmt = parser.parse("INSERT INTO Users (user_id, note) VALUES (1, 'a,b')")
    assert stmt.values == [[1, "a,b"]]


def test_insert_value_with_escaped_quote(parser):
    stmt = parser.parse("INSERT INTO Users (user_id, note) VALUES (1, 'it''s')")
    assert stmt.values == [[1, "it's"]]


def test_insert_multi_row_only_first_row_is_parsed(parser):
    """Known limitation: the INSERT regex captures only the first VALUES group.

    Multi-row INSERT silently parses just the first row. Pinned here so a
    future fix flips this assertion deliberately instead of by accident.
    """
    stmt = parser.parse("INSERT INTO Users (user_id, name) VALUES (1, 'a'), (2, 'b')")
    assert stmt.values == [[1, "a"]]


def test_insert_invalid_syntax_raises(parser):
    with pytest.raises(ValueError, match="Invalid INSERT syntax"):
        parser.parse("INSERT INTO Users VALUES 1, 2")


# ---------------------------------------------------------------------------
# Parser: UPDATE (incl. regressions for the naive SET-clause split bug)
# ---------------------------------------------------------------------------

def test_update_basic(parser):
    stmt = parser.parse("UPDATE Users SET name = 'Bob' WHERE user_id = 123")
    assert isinstance(stmt, UpdateStatement)
    assert stmt.table == "Users"
    assert stmt.set_clause == {"name": "Bob"}
    assert stmt.where_clause == Comparison(column="user_id", op="=", literal=123)
    assert stmt.is_read_only() is False


def test_update_set_value_containing_equals(parser):
    """Regression: pair.split('=') used to explode on '=' inside the value."""
    stmt = parser.parse("UPDATE Users SET note = 'a=b' WHERE user_id = 1")
    assert stmt.set_clause == {"note": "a=b"}


def test_update_set_value_containing_comma(parser):
    """Regression: set_str.split(',') used to split inside quoted values."""
    stmt = parser.parse("UPDATE Users SET note = 'a,b' WHERE user_id = 1")
    assert stmt.set_clause == {"note": "a,b"}


def test_update_multiple_pairs_with_quoted_separators(parser):
    stmt = parser.parse(
        "UPDATE Users SET note = 'a=b', tag = 'x,y', age = 30 WHERE user_id = 1")
    assert stmt.set_clause == {"note": "a=b", "tag": "x,y", "age": 30}


def test_update_set_value_with_escaped_quote_and_separators(parser):
    stmt = parser.parse("UPDATE Users SET note = 'it''s = fine, ok' WHERE user_id = 1")
    assert stmt.set_clause == {"note": "it's = fine, ok"}


def test_update_set_numeric_and_null_values(parser):
    # NOTE: SET col = TRUE is not covered: sqlparse splits "col = TRUE" into
    # separate top-level tokens instead of one Comparison, so the SET clause
    # never reaches the pair splitter (pre-existing tokenization limitation).
    stmt = parser.parse("UPDATE Users SET age = 30, score = 1.5 WHERE user_id = 1")
    assert stmt.set_clause == {"age": 30, "score": 1.5}
    stmt = parser.parse("UPDATE Users SET note = NULL WHERE user_id = 1")
    assert stmt.set_clause == {"note": None}


def test_update_without_where(parser):
    stmt = parser.parse("UPDATE Users SET age = 30")
    assert stmt.set_clause == {"age": 30}
    assert stmt.where_clause is None


def test_update_malformed_set_pair_raises(parser):
    with pytest.raises(ValueError, match="Invalid SET clause pair"):
        parser.parse("UPDATE Users SET note WHERE user_id = 1")


# ---------------------------------------------------------------------------
# Parser: DELETE
# ---------------------------------------------------------------------------

def test_delete_with_where(parser):
    stmt = parser.parse("DELETE FROM Users WHERE user_id = 123")
    assert isinstance(stmt, DeleteStatement)
    assert stmt.table == "Users"
    assert stmt.where_clause == Comparison(column="user_id", op="=", literal=123)
    assert stmt.is_read_only() is False


def test_delete_without_where(parser):
    stmt = parser.parse("DELETE FROM Users")
    assert stmt.table == "Users"
    assert stmt.where_clause is None


# ---------------------------------------------------------------------------
# Parser: CREATE TABLE
# ---------------------------------------------------------------------------

def test_create_table_pk_outside_parens(parser):
    stmt = parser.parse(
        "CREATE TABLE Users (user_id INT64 NOT NULL, name STRING(100)) "
        "PRIMARY KEY (user_id)")
    assert isinstance(stmt, CreateTableStatement)
    assert stmt.table == "Users"
    assert stmt.columns == [
        {"name": "user_id", "type": "INT64"},
        {"name": "name", "type": "STRING"},
    ]
    assert stmt.primary_key == ["user_id"]
    assert stmt.is_read_only() is False


def test_create_table_composite_primary_key(parser):
    stmt = parser.parse(
        "CREATE TABLE Orders (order_id INT64, user_id INT64) "
        "PRIMARY KEY (user_id, order_id)")
    assert stmt.primary_key == ["user_id", "order_id"]


def test_create_table_pk_inside_parens(parser):
    stmt = parser.parse("CREATE TABLE T (id INT64, PRIMARY KEY (id))")
    assert stmt.columns == [{"name": "id", "type": "INT64"}]
    assert stmt.primary_key == ["id"]


def test_create_table_invalid_raises(parser):
    with pytest.raises(ValueError, match="Invalid CREATE TABLE syntax"):
        parser.parse("CREATE TABLE")


# ---------------------------------------------------------------------------
# Parser: malformed SQL
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sql,fragment", [
    ("", "Empty SQL query"),
    ("GRANT ALL ON Users", "Unsupported statement type"),
])
def test_parser_rejects_unsupported_sql(parser, sql, fragment):
    with pytest.raises(ValueError, match=fragment):
        parser.parse(sql)


def test_parser_where_syntax_error_raises_predicate_error(parser):
    # PredicateError subclasses ValueError, so callers catching ValueError
    # still see parse failures as hard errors (never "match everything").
    with pytest.raises(PredicateError):
        parser.parse("SELECT * FROM Users WHERE age >")


# ---------------------------------------------------------------------------
# Predicates: parse_literal_text
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("42", 42),
    ("-7", -7),
    ("0", 0),
    ("  42  ", 42),
    ("1.5", 1.5),
    ("-2.25", -2.25),
    ("'hello'", "hello"),
    ("'a=b, c'", "a=b, c"),
    ("'it''s'", "it's"),
    ("''", ""),
    ("NULL", None),
    ("null", None),
    ("TRUE", True),
    ("true", True),
    ("FALSE", False),
    ("bare_word", "bare_word"),
])
def test_parse_literal_text(text, expected):
    result = parse_literal_text(text)
    assert result == expected
    # 42 == 42.0 and True == 1 in Python; pin the exact type too.
    assert type(result) is type(expected)


# ---------------------------------------------------------------------------
# Predicates: comparison operators
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("predicate,row,expected", [
    ("age = 30", {"age": 30}, True),
    ("age = 30", {"age": 31}, False),
    ("age != 30", {"age": 31}, True),
    ("age != 30", {"age": 30}, False),
    ("age <> 30", {"age": 31}, True),
    ("age <> 30", {"age": 30}, False),
    ("age < 30", {"age": 29}, True),
    ("age < 30", {"age": 30}, False),
    ("age <= 30", {"age": 30}, True),
    ("age <= 30", {"age": 31}, False),
    ("age > 30", {"age": 31}, True),
    ("age > 30", {"age": 30}, False),
    ("age >= 30", {"age": 30}, True),
    ("age >= 30", {"age": 29}, False),
    ("name = 'Alice'", {"name": "Alice"}, True),
    ("name != 'Alice'", {"name": "Bob"}, True),
    ("name < 'B'", {"name": "Alice"}, True),
    ("score > 1.5", {"score": 2.0}, True),
    ("score <= 1.5", {"score": 1.5}, True),
    ("active = TRUE", {"active": True}, True),
    ("active = FALSE", {"active": True}, False),
])
def test_comparison_operators(predicate, row, expected):
    assert parse_predicate(predicate).evaluate(row) is expected


def test_predicate_string_literal_with_separators():
    pred = parse_predicate("note = 'a=b, c'")
    assert pred == Comparison(column="note", op="=", literal="a=b, c")
    assert pred.evaluate({"note": "a=b, c"}) is True


def test_predicate_string_literal_with_escaped_quote():
    pred = parse_predicate("name = 'it''s'")
    assert pred.literal == "it's"
    assert pred.evaluate({"name": "it's"}) is True


# ---------------------------------------------------------------------------
# Predicates: logical trees
# ---------------------------------------------------------------------------

def test_predicate_tree_structure():
    node = parse_predicate("age > 21 AND city = 'NYC'")
    assert node == Logical("AND", [
        Comparison(column="age", op=">", literal=21),
        Comparison(column="city", op="=", literal="NYC"),
    ])


def test_and_or_not_evaluation():
    both = parse_predicate("age >= 18 AND age < 65")
    assert both.evaluate({"age": 30}) is True
    assert both.evaluate({"age": 70}) is False

    either = parse_predicate("age < 18 OR age > 65")
    assert either.evaluate({"age": 70}) is True
    assert either.evaluate({"age": 30}) is False

    negated = parse_predicate("NOT age = 30")
    assert isinstance(negated, Not)
    assert negated.evaluate({"age": 31}) is True
    assert negated.evaluate({"age": 30}) is False


def test_and_binds_tighter_than_or():
    pred = parse_predicate("a = 1 OR b = 2 AND c = 3")
    assert pred.evaluate({"a": 1, "b": 0, "c": 0}) is True
    assert pred.evaluate({"a": 0, "b": 2, "c": 3}) is True
    assert pred.evaluate({"a": 0, "b": 2, "c": 0}) is False


def test_parentheses_override_precedence():
    pred = parse_predicate("(city = 'NYC' OR city = 'SF') AND age > 21")
    assert pred.evaluate({"city": "SF", "age": 30}) is True
    assert pred.evaluate({"city": "SF", "age": 20}) is False
    assert pred.evaluate({"city": "LA", "age": 30}) is False


def test_null_and_missing_column_semantics():
    """SQL-ish semantics: comparisons involving NULL / missing columns are False."""
    assert parse_predicate("age = 30").evaluate({}) is False
    assert parse_predicate("age = 30").evaluate({"age": None}) is False
    assert parse_predicate("age != 30").evaluate({}) is False
    # Even NULL = NULL is not a match (no SQL trichotomy, just False).
    assert parse_predicate("age = NULL").evaluate({"age": None}) is False


def test_type_mismatch_evaluates_false():
    assert parse_predicate("age > 'abc'").evaluate({"age": 30}) is False
    assert parse_predicate("age = 'abc'").evaluate({"age": 30}) is False


def test_qualified_column_name_is_stripped():
    assert parse_predicate("users.age > 21") == Comparison(column="age", op=">", literal=21)


def test_evaluate_module_function():
    assert evaluate(parse_predicate("age > 1"), {"age": 2}) is True


# ---------------------------------------------------------------------------
# Predicates: malformed input
# ---------------------------------------------------------------------------

def test_predicate_error_is_value_error():
    """The parser's contract: PredicateError must stay a ValueError subclass."""
    assert issubclass(PredicateError, ValueError)


@pytest.mark.parametrize("text,fragment", [
    ("", "Empty WHERE clause"),
    ("age >", "Unexpected end"),
    ("age > 21 name = 'x'", "Trailing tokens"),
    ("(age > 21", "Unbalanced parenthesis"),
    ("42 > age", "Expected column name"),
    ("age > name", "Expected literal"),
    ("age @ 3", "Unexpected character"),
])
def test_parse_predicate_rejects_malformed(text, fragment):
    with pytest.raises(PredicateError, match=fragment):
        parse_predicate(text)


# ---------------------------------------------------------------------------
# Executor (real MVCCStore + SchemaRegistry, no transaction)
# ---------------------------------------------------------------------------

def run_sql(executor, sql):
    """Parse and execute a SQL string end-to-end."""
    return executor.execute(SQLParser().parse(sql))


@pytest.fixture
def executor(tmp_path):
    store = MVCCStore(tmp_path / "db", HybridLogicalClock("sql-test-node"))
    ex = QueryExecutor(store, SchemaRegistry())
    yield ex
    store.close()


@pytest.fixture
def users_db(executor):
    """Executor with a Users table and three seed rows."""
    run_sql(executor,
            "CREATE TABLE Users (user_id INT64 NOT NULL, name STRING(100), "
            "age INT64, note STRING(200)) PRIMARY KEY (user_id)")
    run_sql(executor, "INSERT INTO Users (user_id, name, age) VALUES (1, 'Alice', 34)")
    run_sql(executor, "INSERT INTO Users (user_id, name, age) VALUES (2, 'Bob', 19)")
    run_sql(executor, "INSERT INTO Users (user_id, name, age) VALUES (3, 'Cara', 27)")
    return executor


def test_query_result_len_and_iter():
    result = QueryResult(rows=[{"a": 1}, {"a": 2}])
    assert len(result) == 2
    assert list(result) == [{"a": 1}, {"a": 2}]
    empty = QueryResult()
    assert len(empty) == 0
    assert empty.affected_rows == 0


def test_create_table_registers_schema_with_type_aliases(executor):
    run_sql(executor,
            "CREATE TABLE T (id INT NOT NULL, name VARCHAR(50), score FLOAT) "
            "PRIMARY KEY (id)")
    schema = executor.schema_registry.get_table("T")
    assert schema is not None
    assert schema.primary_key == ["id"]
    assert schema.columns["id"].type == ColumnType.INT64      # INT alias
    assert schema.columns["name"].type == ColumnType.STRING   # VARCHAR alias
    assert schema.columns["score"].type == ColumnType.FLOAT64  # FLOAT alias


def test_create_duplicate_table_raises(executor):
    run_sql(executor, "CREATE TABLE T (id INT64) PRIMARY KEY (id)")
    with pytest.raises(ValueError, match="already exists"):
        run_sql(executor, "CREATE TABLE T (id INT64) PRIMARY KEY (id)")


def test_create_table_unknown_type_raises(executor):
    with pytest.raises(SchemaError, match="Unknown column type"):
        run_sql(executor, "CREATE TABLE T (id WIDGET) PRIMARY KEY (id)")


def test_insert_then_select_star_roundtrip(users_db):
    result = run_sql(users_db, "SELECT * FROM Users")
    rows = sorted(result.rows, key=lambda r: r["user_id"])
    assert rows == [
        {"user_id": 1, "name": "Alice", "age": 34},
        {"user_id": 2, "name": "Bob", "age": 19},
        {"user_id": 3, "name": "Cara", "age": 27},
    ]


def test_insert_reports_affected_rows(users_db):
    result = run_sql(users_db,
                     "INSERT INTO Users (user_id, name, age) VALUES (4, 'Dan', 41)")
    assert result.affected_rows == 1
    assert len(run_sql(users_db, "SELECT * FROM Users")) == 4


def test_select_where_and_filters_rows(users_db):
    result = run_sql(users_db, "SELECT * FROM Users WHERE age > 20 AND age < 30")
    assert [r["name"] for r in result.rows] == ["Cara"]


def test_select_where_or_filters_rows(users_db):
    result = run_sql(users_db,
                     "SELECT * FROM Users WHERE user_id = 1 OR user_id = 3")
    assert {r["name"] for r in result.rows} == {"Alice", "Cara"}


def test_select_projection(users_db):
    result = run_sql(users_db, "SELECT name FROM Users WHERE user_id = 1")
    assert result.rows == [{"name": "Alice"}]


def test_select_order_by_desc_and_limit(users_db):
    result = run_sql(users_db, "SELECT * FROM Users ORDER BY age DESC LIMIT 2")
    assert [r["age"] for r in result.rows] == [34, 27]


def test_select_offset(users_db):
    result = run_sql(users_db, "SELECT * FROM Users ORDER BY age LIMIT 10 OFFSET 1")
    assert [r["age"] for r in result.rows] == [27, 34]


def test_select_unknown_table_raises(executor):
    with pytest.raises(ValueError, match="not found"):
        run_sql(executor, "SELECT * FROM Nope")


def test_insert_unknown_table_raises(executor):
    with pytest.raises(ValueError, match="not found"):
        run_sql(executor, "INSERT INTO Nope (id) VALUES (1)")


def test_insert_missing_primary_key_raises(users_db):
    with pytest.raises(ValueError, match="Primary key column"):
        run_sql(users_db, "INSERT INTO Users (name, age) VALUES ('Eve', 50)")


def test_update_applies_only_to_matching_rows(users_db):
    result = run_sql(users_db, "UPDATE Users SET age = 99 WHERE name = 'Bob'")
    assert result.affected_rows == 1
    rows = {r["name"]: r["age"] for r in run_sql(users_db, "SELECT * FROM Users")}
    assert rows == {"Alice": 34, "Bob": 99, "Cara": 27}


def test_update_set_value_with_equals_and_comma_roundtrip(users_db):
    """End-to-end regression for the naive SET-clause split bug."""
    result = run_sql(users_db,
                     "UPDATE Users SET note = 'k=v, x=y', name = 'A,B' "
                     "WHERE user_id = 1")
    assert result.affected_rows == 1
    row = run_sql(users_db, "SELECT * FROM Users WHERE user_id = 1").rows[0]
    assert row["note"] == "k=v, x=y"
    assert row["name"] == "A,B"


def test_update_without_where_updates_all_rows(users_db):
    result = run_sql(users_db, "UPDATE Users SET age = 0")
    assert result.affected_rows == 3
    assert all(r["age"] == 0 for r in run_sql(users_db, "SELECT * FROM Users"))


def test_delete_removes_only_matching_rows(users_db):
    result = run_sql(users_db, "DELETE FROM Users WHERE age < 21")
    assert result.affected_rows == 1
    remaining = {r["user_id"] for r in run_sql(users_db, "SELECT * FROM Users")}
    assert remaining == {1, 3}


def test_delete_without_where_removes_all_rows(users_db):
    result = run_sql(users_db, "DELETE FROM Users")
    assert result.affected_rows == 3
    assert len(run_sql(users_db, "SELECT * FROM Users")) == 0


def test_executor_rejects_non_statement(executor):
    with pytest.raises(ValueError, match="Unsupported statement type"):
        executor.execute("SELECT 1")
