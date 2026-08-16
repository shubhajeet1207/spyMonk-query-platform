"""
Query planning helpers: multi-table extraction (JOINs work) and Snowflake-style
zone-map pruning. Pruning is ONLY an optimization: a partition is skipped only
when its zone map PROVES no row can match; any uncertainty loads the partition.
SQLite always executes the full query over whatever was loaded.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

import sqlparse
from sqlparse import tokens as T
from sqlparse.sql import Identifier, IdentifierList, Parenthesis, Where


# ---------- table extraction ----------

# A CTE definition looks like `WITH name AS (` or, for additional CTEs in the
# same WITH clause, `, name AS (`. This is distinct from a table alias (`FROM
# t AS alias`, name follows AS) or a derived-table alias (`(SELECT ...) AS
# alias`, no "(" right after AS) -- "AS" immediately followed by "(" is
# specific to a CTE's `name AS (subquery)` form.
_CTE_RE = re.compile(r'(?:\bWITH\b|,)\s+([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(', re.I)


def extract_tables(sql: str) -> List[str]:
    parsed = sqlparse.parse(sql)
    if not parsed:
        return []
    tables: List[str] = []
    seen = set()

    def add(name: Optional[str]):
        if name:
            key = name.strip('`"').lower()
            if key not in seen:
                seen.add(key)
                tables.append(name.strip('`"'))

    def handle_source(tok):
        if isinstance(tok, IdentifierList):
            for ident in tok.get_identifiers():
                handle_source(ident)
        elif isinstance(tok, Identifier):
            if any(isinstance(t, Parenthesis) for t in tok.tokens):
                walk(tok)                     # (SELECT ...) alias -> recurse
            else:
                add(tok.get_real_name())
        elif isinstance(tok, Parenthesis):
            walk(tok)
        elif tok.ttype in (T.Name, T.Keyword):  # bare name sqlparse kept as keyword
            add(str(tok))

    def walk(token_list):
        expecting = False
        for tok in token_list.tokens:
            if tok.is_whitespace:
                continue
            val = tok.value.upper() if tok.ttype is T.Keyword else ""
            if tok.ttype is T.Keyword and (val == "FROM" or val.endswith("JOIN")):
                expecting = True
                continue
            if expecting:
                handle_source(tok)
                expecting = False
                continue
            if isinstance(tok, (Parenthesis, Where, Identifier, IdentifierList)):
                walk(tok)

    for stmt in parsed:
        walk(stmt)

    # A CTE name is a query-local alias, never a stored table -- exclude any
    # name the walk above picked up (e.g. from a trailing `FROM <cte_name>`)
    # that is actually defined by a top-level WITH clause.
    cte_names = {m.group(1).lower() for m in _CTE_RE.finditer(sql)}
    if cte_names:
        tables = [t for t in tables if t.strip('`"').lower() not in cte_names]
    return tables


# ---------- predicate extraction ----------

_COMPARISON_RE = re.compile(
    r'^([\w."`]+)\s*(<=|>=|<>|!=|=|<|>)\s*(.+)$', re.S)
_IN_RE = re.compile(r'^([\w."`]+)\s+IN\s*\((.+)\)$', re.I | re.S)
_WHERE_RE = re.compile(
    r'\bWHERE\b(.*?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|\bOFFSET\b|$)',
    re.I | re.S)


def _parse_literal(text: str) -> Tuple[bool, Any]:
    """Returns (ok, value). ok=False -> not a usable pruning literal."""
    t = text.strip()
    if len(t) >= 2 and t[0] == "'" and t[-1] == "'":
        return True, t[1:-1].replace("''", "'")
    try:
        return True, int(t)
    except ValueError:
        pass
    try:
        return True, float(t)
    except ValueError:
        return False, None


def _split_top_level(text: str, word: str) -> List[str]:
    """Split on a keyword at paren-depth 0, outside string literals."""
    parts, buf, depth, in_str = [], [], 0, False
    tokens = re.split(rf'(\b{word}\b)', text, flags=re.I)
    for piece in tokens:
        if piece.upper() == word and depth == 0 and not in_str:
            parts.append("".join(buf))
            buf = []
            continue
        for ch in piece:
            if ch == "'":
                in_str = not in_str
            elif not in_str and ch == "(":
                depth += 1
            elif not in_str and ch == ")":
                depth -= 1
        buf.append(piece)
    parts.append("".join(buf))
    return parts


def extract_predicates(sql: str) -> Dict[str, List[Tuple[str, Any]]]:
    m = _WHERE_RE.search(sql)
    if not m:
        return {}
    where = m.group(1).strip()
    if len(_split_top_level(where, "OR")) > 1:
        return {}   # top-level OR: no partition is provably excludable

    preds: Dict[str, List[Tuple[str, Any]]] = {}
    for part in _split_top_level(where, "AND"):
        part = part.strip()
        if not part or part.startswith("("):
            continue   # parenthesized group: skip (safe — just no pruning from it)
        in_match = _IN_RE.match(part)
        if in_match:
            col = in_match.group(1).split(".")[-1].strip('`"').lower()
            values = []
            ok_all = True
            for raw in in_match.group(2).split(","):
                ok, val = _parse_literal(raw)
                if not ok:
                    ok_all = False
                    break
                values.append(val)
            if ok_all and values:
                preds.setdefault(col, []).append(("IN", values))
            continue
        comp = _COMPARISON_RE.match(part)
        if not comp:
            continue   # functions, IS NULL, LIKE, ... -> skip conjunct (safe)
        col_raw, op, lit_raw = comp.groups()
        if "(" in col_raw:
            continue
        ok, value = _parse_literal(lit_raw)
        if not ok:
            continue   # column-vs-column or expression: skip
        col = col_raw.split(".")[-1].strip('`"').lower()
        preds.setdefault(col, []).append((op, value))
    return preds


# ---------- partition selection ----------

def _may_match(zone_map: Optional[dict], op: str, value: Any) -> bool:
    """False ONLY when the zone map proves no row in the partition can match."""
    if not zone_map or zone_map.get("min") is None:
        return True
    lo, hi = zone_map["min"], zone_map["max"]
    numeric_zone = isinstance(lo, (int, float)) and not isinstance(lo, bool)

    if op == "IN":
        if not isinstance(value, list):
            return True
        return any(_may_match(zone_map, "=", v) for v in value)

    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return True
    if isinstance(value, str) == numeric_zone:
        return True   # type mismatch: never prune

    if op == "=":
        return lo <= value <= hi
    if op in ("!=", "<>"):
        return not (lo == hi == value)
    if op == "<":
        return lo < value
    if op == "<=":
        return lo <= value
    if op == ">":
        return hi > value
    if op == ">=":
        return hi >= value
    return True


def select_partitions(meta: dict, predicates: Dict[str, List[Tuple[str, Any]]]
                      ) -> Tuple[List[int], int]:
    partitions = meta.get("partitions", [])
    total = len(partitions)
    if not predicates:
        return [p["idx"] for p in partitions], total

    cols = [str(c) for c in meta.get("columns", [])]
    lowered = [c.lower() for c in cols]
    # Columns whose names collide only by case are unresolvable: a predicate
    # on "id" could mean either "id" or "ID". Never prune on such a column --
    # consulting either one's zone map risks consulting the WRONG column's
    # range and silently dropping a partition that could match.
    ambiguous = {c for c in lowered if lowered.count(c) > 1}
    col_map = {c.lower(): c for c in cols}   # last-wins, but ambiguous ones are guarded below
    selected = []
    for part in partitions:
        load = True
        for col, conditions in predicates.items():
            if col in ambiguous:
                continue            # case-colliding column name -> unresolvable -> never prune
            real = col_map.get(col)
            zone_map = part.get("zone_map", {}).get(real) if real else None
            for op, value in conditions:
                if not _may_match(zone_map, op, value):
                    load = False
                    break
            if not load:
                break
        if load:
            selected.append(part["idx"])
    return selected, total
