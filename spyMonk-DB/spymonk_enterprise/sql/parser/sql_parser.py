"""
SQL Parser

Parses SQL queries into AST (Abstract Syntax Tree).
Uses sqlparse library for tokenization, then builds AST.
"""

import re
import sqlparse
from sqlparse import tokens as T
from sqlparse.sql import Token, Identifier, Where, Comparison
from typing import Union, List, Optional
import logging

from spymonk_enterprise.sql.parser.ast import (
    SelectStatement, InsertStatement, UpdateStatement, DeleteStatement,
    CreateTableStatement, Expression, ExprType, ColumnRef
)
from spymonk_enterprise.sql.parser.predicates import parse_predicate, parse_literal_text

logger = logging.getLogger(__name__)


class SQLParser:
    """
    SQL Parser for SpyMonk-DB.

    Supports Spanner-compatible SQL syntax.
    """

    def parse(self, sql: str) -> Union[SelectStatement, InsertStatement, UpdateStatement, DeleteStatement, CreateTableStatement]:
        """
        Parse SQL query into AST.

        Args:
            sql: SQL query string

        Returns:
            Statement AST

        Raises:
            ValueError: If SQL is invalid
        """
        # Normalize SQL
        sql = sql.strip()

        # Parse with sqlparse
        parsed = sqlparse.parse(sql)
        if not parsed:
            raise ValueError("Empty SQL query")

        statement = parsed[0]

        # Determine statement type
        stmt_type = statement.get_type()

        if stmt_type == 'SELECT':
            return self._parse_select(statement)
        elif stmt_type == 'INSERT':
            return self._parse_insert(statement)
        elif stmt_type == 'UPDATE':
            return self._parse_update(statement)
        elif stmt_type == 'DELETE':
            return self._parse_delete(statement)
        elif stmt_type == 'CREATE':
            return self._parse_create(statement)
        else:
            raise ValueError(f"Unsupported statement type: {stmt_type}")

    def _parse_select(self, statement) -> SelectStatement:
        """
        Parse SELECT statement.

        Example: SELECT name, email FROM Users WHERE age > 21 LIMIT 10
        """
        tokens = [t for t in statement.tokens if not t.is_whitespace]

        columns = []
        from_table = None
        where_clause = None
        order_by = None
        limit = None
        offset = None

        i = 0
        while i < len(tokens):
            token = tokens[i]

            if token.ttype is T.Keyword.DML and token.value.upper() == 'SELECT':
                # Next token should be column list
                i += 1
                col_token = tokens[i]
                if isinstance(col_token, sqlparse.sql.IdentifierList):
                    columns = [str(ident).strip() for ident in col_token.get_identifiers()]
                elif isinstance(col_token, sqlparse.sql.Identifier):
                    columns = [str(col_token).strip()]
                else:
                    columns = [str(col_token).strip()]

            elif token.ttype is T.Keyword and token.value.upper() == 'FROM':
                # Next token is table name
                i += 1
                from_table = str(tokens[i]).strip()

            elif isinstance(token, sqlparse.sql.Where):
                # WHERE clause
                where_clause = self._parse_where(token)

            elif token.ttype is T.Keyword and token.value.upper() in ('ORDER BY', 'ORDER'):
                i += 1
                order_by = self._parse_order_by(str(tokens[i]))

            elif token.ttype is T.Keyword and token.value.upper() == 'LIMIT':
                # Next token is limit value
                i += 1
                limit = int(str(tokens[i]).strip())

            elif token.ttype is T.Keyword and token.value.upper() == 'OFFSET':
                i += 1
                offset = int(str(tokens[i]).strip())

            i += 1

        if not from_table:
            raise ValueError("SELECT statement missing FROM clause")

        return SelectStatement(
            columns=columns,
            from_table=from_table,
            where_clause=where_clause,
            order_by=order_by,
            limit=limit,
            offset=offset
        )

    def _parse_insert(self, statement) -> InsertStatement:
        """
        Parse INSERT statement.
        """
        sql = str(statement).strip()
        
        # Regex to match INSERT INTO Table (cols) VALUES (vals)
        match = re.search(r'INSERT\s+INTO\s+(\w+)\s*\((.*?)\)\s*VALUES\s*\((.*?)\)', sql, re.IGNORECASE | re.DOTALL)
        if not match:
            raise ValueError("Invalid INSERT syntax")
            
        table = match.group(1)
        columns = [c.strip() for c in match.group(2).split(',')]
        
        # Values might be multiple rows (simplified: handle one row)
        values_str = match.group(3)
        values = [[parse_literal_text(v) for v in self._split_csv(values_str)]]
        
        return InsertStatement(
            table=table,
            columns=columns,
            values=values
        )

    def _parse_update(self, statement) -> UpdateStatement:
        """
        Parse UPDATE statement.

        Example: UPDATE Users SET name = 'Bob' WHERE user_id = 123
        """
        tokens = [t for t in statement.tokens if not t.is_whitespace]

        table = None
        set_clause = {}
        where_clause = None

        i = 0
        while i < len(tokens):
            token = tokens[i]

            if token.ttype is T.Keyword.DML and token.value.upper() == 'UPDATE':
                # Next token is table name
                i += 1
                table = str(tokens[i]).strip()

            elif token.ttype is T.Keyword and token.value.upper() == 'SET':
                # Parse SET clause
                i += 1
                # Quote-aware parsing: column = value. A ',' or '=' inside a
                # quoted string value (e.g. SET note = 'a=b, c') must not split.
                set_str = str(tokens[i]).strip()
                pairs = self._split_csv(set_str)
                for pair in pairs:
                    col, val = self._split_set_pair(pair)
                    set_clause[col.strip()] = parse_literal_text(val)

            elif isinstance(token, sqlparse.sql.Where):
                where_clause = self._parse_where(token)

            i += 1

        return UpdateStatement(
            table=table,
            set_clause=set_clause,
            where_clause=where_clause
        )

    def _parse_delete(self, statement) -> DeleteStatement:
        """
        Parse DELETE statement.

        Example: DELETE FROM Users WHERE user_id = 123
        """
        tokens = [t for t in statement.tokens if not t.is_whitespace]

        table = None
        where_clause = None

        i = 0
        while i < len(tokens):
            token = tokens[i]

            if token.ttype is T.Keyword and token.value.upper() == 'FROM':
                # Next token is table name
                i += 1
                table = str(tokens[i]).strip()

            elif isinstance(token, sqlparse.sql.Where):
                where_clause = self._parse_where(token)

            i += 1

        return DeleteStatement(
            table=table,
            where_clause=where_clause
        )

    def _parse_create(self, statement) -> CreateTableStatement:
        """
        Parse CREATE TABLE statement.
        """
        sql = str(statement).strip()

        # Extract table name
        match = re.search(r'CREATE\s+TABLE\s+(\w+)', sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid CREATE TABLE syntax")

        table = match.group(1)

        # Extract columns and primary key
        # We need to find the first balanced set of parentheses for columns
        depth = 0
        first_paren = -1
        last_paren = -1
        for i, char in enumerate(sql):
            if char == '(':
                if depth == 0: first_paren = i
                depth += 1
            elif char == ')':
                depth -= 1
                if depth == 0:
                    last_paren = i
                    break
        
        if first_paren == -1 or last_paren == -1:
            raise ValueError("CREATE TABLE missing column definitions")

        content = sql[first_paren+1:last_paren]
        
        # Split by comma
        parts = [p.strip() for p in content.split(',')]
        
        columns = []
        primary_key = []

        for part in parts:
            if not part: continue
            if part.upper().startswith('PRIMARY KEY'):
                pk_match = re.search(r'PRIMARY\s+KEY\s*\((.*?)\)', part, re.IGNORECASE)
                if pk_match:
                    primary_key = [c.strip() for c in pk_match.group(1).split(',')]
                continue
                
            # Assume column definition: name type [constraints]
            col_parts = part.split()
            if len(col_parts) >= 2:
                col_type = col_parts[1].upper().split('(')[0].strip()
                columns.append({
                    'name': col_parts[0],
                    'type': col_type
                })

        # Check for PRIMARY KEY outside the parentheses
        remaining_sql = sql[last_paren+1:]
        pk_match = re.search(r'PRIMARY\s+KEY\s*\((.*?)\)', remaining_sql, re.IGNORECASE)
        if pk_match:
            primary_key = [c.strip() for c in pk_match.group(1).split(',')]

        return CreateTableStatement(
            table=table,
            columns=columns,
            primary_key=primary_key
        )

    def _parse_where(self, where_token):
        """Parse WHERE clause into an evaluable predicate tree (hard error on failure)."""
        text = str(where_token).strip()
        if text.upper().startswith('WHERE'):
            text = text[5:]
        return parse_predicate(text)   # raises PredicateError (a ValueError)

    def _parse_order_by(self, text: str):
        """'age DESC, name' -> [('age', 'DESC'), ('name', 'ASC')]"""
        result = []
        for part in text.split(','):
            bits = part.strip().split()
            if not bits:
                continue
            direction = 'DESC' if len(bits) > 1 and bits[-1].upper() == 'DESC' else 'ASC'
            # Strip a table qualifier: users.age -> age (matches predicates.py).
            result.append((bits[0].split(".")[-1], direction))
        return result

    def _split_set_pair(self, pair: str):
        """Split 'col = value' on the first '=' outside single-quoted strings."""
        in_str = False
        for i, ch in enumerate(pair):
            if ch == "'":
                in_str = not in_str
            elif ch == '=' and not in_str:
                return pair[:i], pair[i + 1:]
        raise ValueError(f"Invalid SET clause pair: {pair!r}")

    def _split_csv(self, text: str):
        """Quote-aware CSV split: a comma inside 'a,b' must not split."""
        parts, buf, in_str = [], [], False
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "'":
                if in_str and i + 1 < len(text) and text[i + 1] == "'":
                    buf.append("''"); i += 2; continue
                in_str = not in_str
                buf.append(ch)
            elif ch == ',' and not in_str:
                parts.append(''.join(buf)); buf = []
            else:
                buf.append(ch)
            i += 1
        parts.append(''.join(buf))
        return [p.strip() for p in parts]
