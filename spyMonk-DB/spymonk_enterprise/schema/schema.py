"""
Table Schema Definitions

Spanner-style schema with:
- Primary keys
- Column types
- Indexes
- Table interleaving (co-location)
"""

from enum import Enum
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class SchemaError(ValueError):
    """Invalid schema definition (e.g., unknown column type)."""


class ColumnType(Enum):
    """SQL column types"""
    INT64 = "INT64"
    FLOAT64 = "FLOAT64"
    BOOL = "BOOL"
    STRING = "STRING"
    BYTES = "BYTES"
    TIMESTAMP = "TIMESTAMP"
    DATE = "DATE"
    ARRAY = "ARRAY"
    STRUCT = "STRUCT"


@dataclass
class ColumnDef:
    """Column definition"""
    name: str
    type: ColumnType
    nullable: bool = True
    max_length: Optional[int] = None  # For STRING/BYTES
    default_value: Optional[Any] = None


@dataclass
class IndexDef:
    """Index definition"""
    name: str
    columns: List[str]
    unique: bool = False
    storing: Optional[List[str]] = None  # Stored columns (Spanner feature)


class TableSchema:
    """
    Spanner-style table schema.

    Example:
        CREATE TABLE Users (
            user_id INT64 NOT NULL,
            name STRING(100),
            email STRING(255),
            created_at TIMESTAMP,
        ) PRIMARY KEY (user_id);

        CREATE INDEX idx_email ON Users(email);
    """

    def __init__(
        self,
        table_name: str,
        columns: List[ColumnDef],
        primary_key: List[str],
        parent_table: Optional[str] = None,
        indexes: Optional[List[IndexDef]] = None
    ):
        """
        Initialize table schema.

        Args:
            table_name: Name of the table
            columns: List of column definitions
            primary_key: Primary key columns (ordered)
            parent_table: Parent table name (for interleaving)
            indexes: Secondary indexes
        """
        self.table_name = table_name
        self.columns = {col.name: col for col in columns}
        self.primary_key = primary_key
        self.parent_table = parent_table
        self.indexes = indexes or []

        # Validate primary key
        for pk_col in primary_key:
            if pk_col not in self.columns:
                raise ValueError(f"Primary key column '{pk_col}' not in table columns")

        logger.info(f"Created schema for table {table_name}")

    def get_column(self, name: str) -> Optional[ColumnDef]:
        """Get column definition"""
        return self.columns.get(name)

    def validate_row(self, row: Dict[str, Any]) -> bool:
        """
        Validate a row against schema.

        Returns:
            True if valid
        """
        # Check all non-nullable columns are present
        for col_name, col_def in self.columns.items():
            if not col_def.nullable and col_name not in row:
                raise ValueError(f"Required column '{col_name}' missing")

        # Check primary key columns
        for pk_col in self.primary_key:
            if pk_col not in row:
                raise ValueError(f"Primary key column '{pk_col}' missing")

        return True

    def encode_primary_key(self, row: Dict[str, Any]) -> bytes:
        """
        Encode primary key from row.

        Spanner's key encoding: Ordered encoding for range scans.

        Returns:
            Encoded key bytes
        """
        key_parts = []
        for pk_col in self.primary_key:
            value = row[pk_col]
            # Simple encoding (production would use proper ordered encoding)
            key_parts.append(str(value).encode('utf-8'))

        # Join with separator
        return b'#'.join(key_parts)

    def __repr__(self) -> str:
        return f"TableSchema(name={self.table_name}, columns={len(self.columns)}, pk={self.primary_key})"


class SchemaRegistry:
    """
    Registry of all table schemas in the database.

    Spanner's universe layer keeps global schema metadata.
    """

    def __init__(self):
        self.tables: Dict[str, TableSchema] = {}

        logger.info("Initialized schema registry")

    def create_table(self, schema: TableSchema):
        """Register a new table schema"""
        if schema.table_name in self.tables:
            raise ValueError(f"Table '{schema.table_name}' already exists")

        # If interleaved, check parent exists
        if schema.parent_table and schema.parent_table not in self.tables:
            raise ValueError(f"Parent table '{schema.parent_table}' not found")

        self.tables[schema.table_name] = schema
        logger.info(f"Registered table '{schema.table_name}'")

    def get_table(self, table_name: str) -> Optional[TableSchema]:
        """Get table schema"""
        return self.tables.get(table_name)

    def drop_table(self, table_name: str):
        """Drop a table schema"""
        if table_name in self.tables:
            del self.tables[table_name]
            logger.info(f"Dropped table '{table_name}'")

    def list_tables(self) -> List[str]:
        """List all table names"""
        return list(self.tables.keys())
