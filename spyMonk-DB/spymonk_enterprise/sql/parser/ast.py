"""
Abstract Syntax Tree (AST) for SQL queries.

Represents parsed SQL queries in a structured format.
"""

from enum import Enum
from dataclasses import dataclass
from typing import List, Optional, Any


class StatementType(Enum):
    """Type of SQL statement"""
    SELECT = "SELECT"
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    CREATE_TABLE = "CREATE_TABLE"
    DROP_TABLE = "DROP_TABLE"
    CREATE_INDEX = "CREATE_INDEX"


class ExprType(Enum):
    """Type of expression"""
    LITERAL = "LITERAL"
    COLUMN = "COLUMN"
    BINARY_OP = "BINARY_OP"
    FUNCTION = "FUNCTION"


@dataclass
class Expression:
    """Base expression class"""
    expr_type: ExprType
    value: Any


@dataclass
class ColumnRef:
    """Column reference: table.column"""
    table: Optional[str]
    column: str


@dataclass
class SelectStatement:
    """
    SELECT statement AST.

    Example:
        SELECT name, email FROM Users WHERE age > 21 ORDER BY name LIMIT 10
    """
    columns: List[str]  # Column names or '*'
    from_table: str
    where_clause: Optional[Expression] = None
    order_by: Optional[List[tuple]] = None  # [(column, 'ASC'|'DESC')]
    limit: Optional[int] = None
    offset: Optional[int] = None

    def is_read_only(self) -> bool:
        return True


@dataclass
class InsertStatement:
    """
    INSERT statement AST.

    Example:
        INSERT INTO Users (name, email) VALUES ('Alice', 'alice@example.com')
    """
    table: str
    columns: List[str]
    values: List[List[Any]]  # List of rows

    def is_read_only(self) -> bool:
        return False


@dataclass
class UpdateStatement:
    """
    UPDATE statement AST.

    Example:
        UPDATE Users SET name = 'Bob' WHERE user_id = 123
    """
    table: str
    set_clause: dict  # {column: value}
    where_clause: Optional[Expression] = None

    def is_read_only(self) -> bool:
        return False


@dataclass
class DeleteStatement:
    """
    DELETE statement AST.

    Example:
        DELETE FROM Users WHERE user_id = 123
    """
    table: str
    where_clause: Optional[Expression] = None

    def is_read_only(self) -> bool:
        return False


@dataclass
class CreateTableStatement:
    """
    CREATE TABLE statement AST.

    Example:
        CREATE TABLE Users (
            user_id INT64 NOT NULL,
            name STRING(100)
        ) PRIMARY KEY (user_id)
    """
    table: str
    columns: List[dict]  # [{name, type, nullable, ...}]
    primary_key: List[str]
    parent_table: Optional[str] = None

    def is_read_only(self) -> bool:
        return False
