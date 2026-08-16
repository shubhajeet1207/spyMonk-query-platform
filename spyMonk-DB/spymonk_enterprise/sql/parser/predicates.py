"""
WHERE-clause predicates: tokenizer + recursive-descent parser + evaluator.

Grammar:
    expr    := and_expr (OR and_expr)*
    and_expr:= unary (AND unary)*
    unary   := NOT unary | '(' expr ')' | comparison
    comparison := IDENT op literal
    op      := = | != | <> | < | <= | > | >=
    literal := 'string' | number | TRUE | FALSE | NULL

SQL-ish semantics: any comparison against NULL / a missing column / a
mismatched type evaluates to False. Unparseable input raises PredicateError —
callers must treat that as a hard error, never as "match everything".
"""

import re
from dataclasses import dataclass
from typing import Any, List, Optional


class PredicateError(ValueError):
    """WHERE clause could not be parsed."""


_TOKEN_RE = re.compile(r"""
    \s*(?:
        (?P<lparen>\() |
        (?P<rparen>\)) |
        (?P<op><=|>=|<>|!=|=|<|>) |
        (?P<string>'(?:[^']|'')*') |
        (?P<number>-?\d+\.\d*|-?\.\d+|-?\d+) |
        (?P<word>[A-Za-z_][A-Za-z0-9_.]*)
    )""", re.VERBOSE)

_KEYWORDS = {"AND", "OR", "NOT", "TRUE", "FALSE", "NULL"}


def _tokenize(text: str) -> List[tuple]:
    tokens, pos = [], 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        m = _TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            raise PredicateError(f"Unexpected character at {pos!r}: {text[pos:pos+10]!r}")
        pos = m.end()
        kind = m.lastgroup
        value = m.group(kind)
        if kind == "word" and value.upper() in _KEYWORDS:
            tokens.append(("kw", value.upper()))
        else:
            tokens.append((kind, value))
    return tokens


def parse_literal_text(text: str) -> Any:
    """Type a raw SQL literal string (shared with INSERT VALUES / UPDATE SET)."""
    t = text.strip()
    up = t.upper()
    if up == "NULL":
        return None
    if up == "TRUE":
        return True
    if up == "FALSE":
        return False
    if len(t) >= 2 and t[0] == "'" and t[-1] == "'":
        return t[1:-1].replace("''", "'")
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return t.strip("'\"")


@dataclass
class Comparison:
    column: str
    op: str
    literal: Any

    def evaluate(self, row: dict) -> bool:
        value = row.get(self.column)
        if value is None or self.literal is None:
            return False
        try:
            if self.op == "=":
                result = value == self.literal
            elif self.op in ("!=", "<>"):
                result = value != self.literal
            elif self.op == "<":
                result = value < self.literal
            elif self.op == "<=":
                result = value <= self.literal
            elif self.op == ">":
                result = value > self.literal
            else:
                result = value >= self.literal
        except TypeError:
            return False
        if isinstance(result, bool):
            return result
        return False

    # equality/inequality across mismatched types: Python's == already
    # returns False without raising, which matches the intent.


@dataclass
class Logical:
    op: str                     # 'AND' | 'OR'
    operands: List[Any]

    def evaluate(self, row: dict) -> bool:
        if self.op == "AND":
            return all(o.evaluate(row) for o in self.operands)
        return any(o.evaluate(row) for o in self.operands)


@dataclass
class Not:
    operand: Any

    def evaluate(self, row: dict) -> bool:
        return not self.operand.evaluate(row)


class _Parser:
    def __init__(self, tokens: List[tuple]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Optional[tuple]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> tuple:
        tok = self.peek()
        if tok is None:
            raise PredicateError("Unexpected end of WHERE clause")
        self.pos += 1
        return tok

    def parse(self):
        node = self.expr()
        if self.peek() is not None:
            raise PredicateError(f"Trailing tokens in WHERE clause: {self.peek()!r}")
        return node

    def expr(self):
        operands = [self.and_expr()]
        while self.peek() == ("kw", "OR"):
            self.take()
            operands.append(self.and_expr())
        return operands[0] if len(operands) == 1 else Logical("OR", operands)

    def and_expr(self):
        operands = [self.unary()]
        while self.peek() == ("kw", "AND"):
            self.take()
            operands.append(self.unary())
        return operands[0] if len(operands) == 1 else Logical("AND", operands)

    def unary(self):
        tok = self.peek()
        if tok == ("kw", "NOT"):
            self.take()
            return Not(self.unary())
        if tok is not None and tok[0] == "lparen":
            self.take()
            node = self.expr()
            if self.peek() is None or self.take()[0] != "rparen":
                raise PredicateError("Unbalanced parenthesis in WHERE clause")
            return node
        return self.comparison()

    def comparison(self):
        kind, column = self.take()
        if kind != "word":
            raise PredicateError(f"Expected column name, got {column!r}")
        kind, op = self.take()
        if kind != "op":
            raise PredicateError(f"Expected comparison operator after {column!r}")
        kind, raw = self.take()
        if kind == "kw" and raw in ("TRUE", "FALSE", "NULL"):
            literal = {"TRUE": True, "FALSE": False, "NULL": None}[raw]
        elif kind == "string" or kind == "number":
            literal = parse_literal_text(raw)
        else:
            raise PredicateError(f"Expected literal after operator, got {raw!r}")
        # Strip a table qualifier: users.age -> age (single-table executor).
        column = column.split(".")[-1]
        return Comparison(column=column, op=op, literal=literal)


def parse_predicate(text: str):
    tokens = _tokenize(text)
    if not tokens:
        raise PredicateError("Empty WHERE clause")
    return _Parser(tokens).parse()


def evaluate(node, row: dict) -> bool:
    return node.evaluate(row)
