"""
schema.py - Describe the database to the LLM ("schema grounding").

The model has never seen our database. If we only send the question, it
guesses table and column names and invents things like a "PhoneNumber"
column. So with every question we also send a compact description of the
real tables, read live from SQL Server:

    dbo.Orders (
      OrderID int PRIMARY KEY,
      CustomerID int,
      Status nvarchar(20)  -- values: 'Cancelled', 'Delivered', 'Pending', 'Shipped'
      ...
    )
    Relationships:
      dbo.Orders.CustomerID -> dbo.Customers.CustomerID

The small "values:" lists matter: without them the model might write
Status = 'Completed', a value that does not exist.

Only APPROVED_TABLES are described. The Phase 6 validator reuses this list.
"""

from dataclasses import dataclass, field
from functools import lru_cache

from database import run_query

# The only tables the assistant may use (all in the dbo schema).
APPROVED_TABLES: list[str] = ["Customers", "Products", "Orders", "OrderDetails", "Employees"]

# Text columns with at most this many different values get a "values:" hint.
MAX_SAMPLE_VALUES = 12

# Columns whose contents must never be sent to the LLM as examples.
# (Here the model runs locally, but with a cloud API this would be a
#  data-leak: real customer data leaving the company.)
NO_SAMPLE_COLUMNS = {("Customers", "Email")}

_TEXT_TYPES = {"nvarchar", "varchar", "nchar", "char"}


@dataclass
class Column:
    name: str
    data_type: str          # e.g. "nvarchar(50)", "decimal(10,2)", "date"
    is_primary_key: bool = False
    sample_values: list[str] = field(default_factory=list)


@dataclass
class Table:
    name: str
    columns: list[Column]


@dataclass
class ForeignKey:
    table: str
    column: str
    ref_table: str
    ref_column: str


@dataclass
class DatabaseSchema:
    tables: list[Table]
    foreign_keys: list[ForeignKey]

    def to_prompt_text(self) -> str:
        """Compact, SQL-like text that small models understand well."""
        lines: list[str] = []
        for table in self.tables:
            lines.append(f"dbo.{table.name} (")
            for i, col in enumerate(table.columns):
                text = f"  {col.name} {col.data_type}"
                if col.is_primary_key:
                    text += " PRIMARY KEY"
                if i < len(table.columns) - 1:
                    text += ","
                if col.sample_values:
                    values = ", ".join(f"'{v}'" for v in col.sample_values)
                    text += f"  -- values: {values}"
                lines.append(text)
            lines.append(")")
            lines.append("")
        if self.foreign_keys:
            lines.append("Relationships (use these for JOINs):")
            for fk in self.foreign_keys:
                lines.append(f"  dbo.{fk.table}.{fk.column} -> dbo.{fk.ref_table}.{fk.ref_column}")
        return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Reading the schema from SQL Server
# ---------------------------------------------------------------------------

def _placeholders(n: int) -> str:
    return ", ".join("?" * n)


def _format_type(row) -> str:
    data_type = row["DATA_TYPE"]
    if data_type in _TEXT_TYPES:
        length = row["CHARACTER_MAXIMUM_LENGTH"]
        return f"{data_type}({'max' if length == -1 else int(length)})"
    if data_type in ("decimal", "numeric"):
        return f"{data_type}({int(row['NUMERIC_PRECISION'])},{int(row['NUMERIC_SCALE'])})"
    return data_type


def _quote(name: str) -> str:
    """Safely quote an identifier for SQL Server: Name -> [Name]."""
    return "[" + name.replace("]", "]]") + "]"


def _sample_values(table: str, column: str) -> list[str]:
    """Distinct values of a text column, only if there are few of them."""
    result = run_query(
        f"SELECT TOP ({MAX_SAMPLE_VALUES + 1}) v "
        f"FROM (SELECT DISTINCT {_quote(column)} AS v FROM dbo.{_quote(table)} "
        f"      WHERE {_quote(column)} IS NOT NULL) AS d "
        f"ORDER BY v"
    )
    values = [str(v) for v in result.dataframe["v"].tolist()]
    return values if len(values) <= MAX_SAMPLE_VALUES else []


def load_schema() -> DatabaseSchema:
    names = APPROVED_TABLES
    ph = _placeholders(len(names))

    columns = run_query(
        f"""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH,
               NUMERIC_PRECISION, NUMERIC_SCALE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME IN ({ph})
        ORDER BY TABLE_NAME, ORDINAL_POSITION
        """,
        params=names, max_rows=10_000,
    ).dataframe

    primary_keys = run_query(
        f"""
        SELECT ku.TABLE_NAME, ku.COLUMN_NAME
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS AS tc
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE AS ku
          ON ku.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
         AND ku.TABLE_SCHEMA = tc.TABLE_SCHEMA
        WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
          AND tc.TABLE_SCHEMA = 'dbo' AND tc.TABLE_NAME IN ({ph})
        """,
        params=names,
    ).dataframe
    pk_set = set(zip(primary_keys["TABLE_NAME"], primary_keys["COLUMN_NAME"]))

    foreign_keys = run_query(
        f"""
        SELECT OBJECT_NAME(fkc.parent_object_id)     AS TableName,
               pc.name                               AS ColumnName,
               OBJECT_NAME(fkc.referenced_object_id) AS RefTable,
               rc.name                               AS RefColumn
        FROM sys.foreign_key_columns AS fkc
        JOIN sys.columns AS pc ON pc.object_id = fkc.parent_object_id
                              AND pc.column_id = fkc.parent_column_id
        JOIN sys.columns AS rc ON rc.object_id = fkc.referenced_object_id
                              AND rc.column_id = fkc.referenced_column_id
        WHERE OBJECT_NAME(fkc.parent_object_id) IN ({ph})
          AND OBJECT_NAME(fkc.referenced_object_id) IN ({ph})
        ORDER BY TableName, ColumnName
        """,
        params=names + names,
    ).dataframe

    tables: list[Table] = []
    for table_name in names:                       # keep the approved order
        rows = columns[columns["TABLE_NAME"] == table_name]
        cols = []
        for _, row in rows.iterrows():
            col = Column(
                name=row["COLUMN_NAME"],
                data_type=_format_type(row),
                is_primary_key=(table_name, row["COLUMN_NAME"]) in pk_set,
            )
            if (row["DATA_TYPE"] in _TEXT_TYPES and not col.is_primary_key
                    and (table_name, col.name) not in NO_SAMPLE_COLUMNS):
                col.sample_values = _sample_values(table_name, col.name)
            cols.append(col)
        if cols:                                   # skip tables that do not exist
            tables.append(Table(table_name, cols))

    fks = [ForeignKey(r.TableName, r.ColumnName, r.RefTable, r.RefColumn)
           for r in foreign_keys.itertuples(index=False)]
    return DatabaseSchema(tables, fks)


@lru_cache(maxsize=1)
def get_schema() -> DatabaseSchema:
    """Read the schema once and reuse it (it rarely changes)."""
    return load_schema()


def get_schema_text() -> str:
    return get_schema().to_prompt_text()


def clear_cache() -> None:
    get_schema.cache_clear()
