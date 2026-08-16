"""
Query Executor

Executes parsed SQL queries against the storage engine.
"""

from typing import List, Dict, Any, Iterator
import json
import logging

from spymonk_enterprise.sql.parser.ast import (
    SelectStatement, InsertStatement, UpdateStatement, DeleteStatement,
    CreateTableStatement
)
from spymonk_enterprise.storage.mvcc import MVCCStore
from spymonk_enterprise.schema.schema import SchemaRegistry, TableSchema, ColumnDef, ColumnType, SchemaError
from spymonk_enterprise.transaction.transaction import Transaction

logger = logging.getLogger(__name__)

TYPE_ALIASES = {
    'INT': 'INT64', 'INTEGER': 'INT64', 'BIGINT': 'INT64',
    'VARCHAR': 'STRING', 'TEXT': 'STRING', 'CHAR': 'STRING',
    'REAL': 'FLOAT64', 'DOUBLE': 'FLOAT64', 'FLOAT': 'FLOAT64',
    'BOOLEAN': 'BOOL',
}


class QueryResult:
    """Query execution result"""

    def __init__(self, rows: List[Dict[str, Any]] = None, affected_rows: int = 0):
        self.rows = rows if rows is not None else []
        self.affected_rows = affected_rows

    def __iter__(self):
        return iter(self.rows)

    def __len__(self):
        return len(self.rows)


class QueryExecutor:
    """
    Execute SQL queries.

    This is a simplified executor for Phase 3.
    Production would include:
    - Query optimization
    - Join algorithms
    - Aggregation
    - Distributed execution
    """

    def __init__(self, store: MVCCStore, schema_registry: SchemaRegistry):
        self.store = store
        self.schema_registry = schema_registry

        logger.info("Initialized query executor")

    def execute(
        self,
        statement: Any,
        transaction: Transaction = None
    ) -> QueryResult:
        """
        Execute a statement.

        Args:
            statement: Parsed SQL statement (AST)
            transaction: Optional transaction context

        Returns:
            QueryResult
        """
        if isinstance(statement, SelectStatement):
            return self._execute_select(statement, transaction)
        elif isinstance(statement, InsertStatement):
            return self._execute_insert(statement, transaction)
        elif isinstance(statement, UpdateStatement):
            return self._execute_update(statement, transaction)
        elif isinstance(statement, DeleteStatement):
            return self._execute_delete(statement, transaction)
        elif isinstance(statement, CreateTableStatement):
            return self._execute_create_table(statement)
        else:
            raise ValueError(f"Unsupported statement type: {type(statement)}")

    def _execute_select(
        self,
        stmt: SelectStatement,
        transaction: Transaction = None
    ) -> QueryResult:
        """
        Execute SELECT query.

        Simplified implementation:
        - Table scan (no indexes yet)
        - Filter in memory
        - Return results
        """
        # Get schema
        schema = self.schema_registry.get_table(stmt.from_table)
        if not schema:
            raise ValueError(f"Table '{stmt.from_table}' not found")

        # Scan table
        # Key prefix for this table (simplified)
        table_prefix = f"{stmt.from_table}#".encode()

        rows = []
        timestamp = transaction.start_timestamp if transaction else None

        for key, value in self.store.scan(
            start_key=table_prefix,
            end_key=table_prefix + b'\xff',
            timestamp=timestamp
        ):
            # Deserialize row (simplified: assume JSON)
            row = json.loads(value.decode('utf-8'))

            # Apply WHERE clause (hard error on unparseable predicates already
            # raised at parse time; here we just evaluate the predicate tree).
            if stmt.where_clause is not None and not stmt.where_clause.evaluate(row):
                continue

            # Project columns
            if stmt.columns == ['*']:
                rows.append(row)
            else:
                projected = {col: row.get(col) for col in stmt.columns}
                rows.append(projected)

        # ORDER BY / OFFSET / LIMIT are applied after the full scan+filter so
        # ordering is correct regardless of storage/scan order.
        if stmt.order_by:
            for col, direction in reversed(stmt.order_by):
                rows.sort(key=lambda r: (r.get(col) is None, r.get(col)),
                          reverse=(direction == 'DESC'))
        if stmt.offset:
            rows = rows[stmt.offset:]
        if stmt.limit is not None:
            rows = rows[:stmt.limit]

        return QueryResult(rows=rows)

    def _execute_insert(
        self,
        stmt: InsertStatement,
        transaction: Transaction = None
    ) -> QueryResult:
        """
        Execute INSERT query.

        Inserts rows into table using transaction.
        """
        # Get schema
        schema = self.schema_registry.get_table(stmt.table)
        if not schema:
            raise ValueError(f"Table '{stmt.table}' not found")

        affected = 0

        for value_row in stmt.values:
            # Build row dict
            row = {stmt.columns[i]: value_row[i] for i in range(len(stmt.columns))}

            # Validate row
            schema.validate_row(row)

            # Encode key
            key = f"{stmt.table}#".encode() + schema.encode_primary_key(row)

            # Serialize value (simplified: JSON)
            value = json.dumps(row).encode('utf-8')

            # Write via transaction
            if transaction:
                transaction.put(key, value)
            else:
                self.store.put(key, value)

            affected += 1

        return QueryResult(affected_rows=affected)

    def _execute_update(
        self,
        stmt: UpdateStatement,
        transaction: Transaction = None
    ) -> QueryResult:
        """
        Execute UPDATE query.

        Simplified: Read rows, modify, write back.
        """
        # Get schema
        schema = self.schema_registry.get_table(stmt.table)
        if not schema:
            raise ValueError(f"Table '{stmt.table}' not found")

        # Scan table to find matching rows
        table_prefix = f"{stmt.table}#".encode()
        affected = 0

        for key, value in self.store.scan(
            start_key=table_prefix,
            end_key=table_prefix + b'\xff'
        ):
            row = json.loads(value.decode('utf-8'))

            # Apply WHERE clause: only touch rows that match (P0 fix — this
            # used to update every row regardless of WHERE).
            if stmt.where_clause is not None and not stmt.where_clause.evaluate(row):
                continue

            # Update row
            for col, val in stmt.set_clause.items():
                row[col] = val

            # Write back
            new_value = json.dumps(row).encode('utf-8')

            if transaction:
                transaction.put(key, new_value)
            else:
                self.store.put(key, new_value)

            affected += 1

        return QueryResult(affected_rows=affected)

    def _execute_delete(
        self,
        stmt: DeleteStatement,
        transaction: Transaction = None
    ) -> QueryResult:
        """
        Execute DELETE query.

        Simplified: Scan and delete matching rows.
        """
        # Get schema
        schema = self.schema_registry.get_table(stmt.table)
        if not schema:
            raise ValueError(f"Table '{stmt.table}' not found")

        # Scan table
        table_prefix = f"{stmt.table}#".encode()
        affected = 0

        for key, value in self.store.scan(
            start_key=table_prefix,
            end_key=table_prefix + b'\xff'
        ):
            # Apply WHERE clause: only delete rows that match (P0 fix — this
            # used to delete every row regardless of WHERE).
            row = json.loads(value.decode('utf-8'))
            if stmt.where_clause is not None and not stmt.where_clause.evaluate(row):
                continue

            # Delete
            if transaction:
                transaction.delete(key)
            else:
                self.store.delete(key)

            affected += 1

        return QueryResult(affected_rows=affected)

    def _execute_create_table(self, stmt: CreateTableStatement) -> QueryResult:
        """
        Execute CREATE TABLE.

        Registers schema in schema registry.
        """
        # Create schema
        # (Simplified: columns are parsed elsewhere)
        columns = []
        for col in stmt.columns:
            raw_type = col['type'].upper()
            resolved = TYPE_ALIASES.get(raw_type, raw_type)
            try:
                col_type = ColumnType[resolved]
            except KeyError:
                raise SchemaError(f"Unknown column type '{col['type']}' for column '{col['name']}'")
            columns.append(ColumnDef(name=col['name'], type=col_type))

        schema = TableSchema(
            table_name=stmt.table,
            columns=columns,
            primary_key=stmt.primary_key,
            parent_table=stmt.parent_table
        )

        # Register
        self.schema_registry.create_table(schema)

        logger.info(f"Created table '{stmt.table}'")
        return QueryResult(affected_rows=0)
