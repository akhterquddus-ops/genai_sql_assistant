"""
database.py - Everything that talks to SQL Server.

The rest of the application never uses pyodbc directly. It calls:

    run_query(sql)        -> QueryResult (a Pandas DataFrame plus metadata)
    test_connection()     -> (ok, message)       used by the sidebar later
    check_permissions()   -> read-only report    used by the sidebar later
    get_tables()          -> list of table names

Safety features already present in Phase 2:
  * credentials come from .env via config.py
  * login timeout and query timeout
  * a hard maximum on the number of rows returned
  * clean error messages instead of raw driver tracebacks
  * logging of metadata only (row count, duration), never data or passwords

The SQL *validator* (SELECT-only, approved tables, ...) arrives in Phase 6.
"""

import logging
import re
import time
from dataclasses import dataclass
from decimal import Decimal

import pandas as pd
import pyodbc

from config import DatabaseSettings, get_settings

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    """A database problem, with a message that is safe to show to the user."""


@dataclass
class QueryResult:
    dataframe: pd.DataFrame
    row_count: int
    truncated: bool          # True if more rows existed than max_rows
    elapsed_seconds: float


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _odbc_quote(value: str) -> str:
    """Wrap a value in {braces} so characters like ; or = in a password
    cannot break (or inject into) the ODBC connection string."""
    return "{" + value.replace("}", "}}") + "}"


def build_connection_string(settings: DatabaseSettings) -> str:
    parts = [
        f"DRIVER={_odbc_quote(settings.driver)}",
        f"SERVER={settings.server}",
        f"DATABASE={_odbc_quote(settings.database)}",
    ]
    if settings.auth_mode == "windows":
        parts.append("Trusted_Connection=yes")
    else:
        parts.append(f"UID={_odbc_quote(settings.username)}")
        parts.append(f"PWD={_odbc_quote(settings.password)}")

    # ODBC Driver 18 encrypts by default. A local server uses a self-signed
    # certificate, so for development we trust it explicitly.
    parts.append("Encrypt=yes")
    parts.append(f"TrustServerCertificate={'yes' if settings.trust_server_certificate else 'no'}")
    parts.append("APP=GenAI SQL Assistant")   # visible to DBAs in SQL Server
    return ";".join(parts) + ";"


def get_connection(settings: DatabaseSettings | None = None) -> pyodbc.Connection:
    """Open a new connection. The caller must close it."""
    settings = settings or get_settings()
    try:
        conn = pyodbc.connect(
            build_connection_string(settings),
            timeout=settings.login_timeout,   # login timeout (seconds)
            autocommit=True,                  # no open transactions left holding locks
        )
    except pyodbc.Error as exc:
        logger.error("Database connection failed (SQLSTATE %s)", _sqlstate(exc))
        raise DatabaseError(_friendly_message(exc)) from exc

    conn.timeout = settings.query_timeout     # per-query timeout (seconds)
    return conn


# ---------------------------------------------------------------------------
# Running queries
# ---------------------------------------------------------------------------

def run_query(sql: str, params: list | tuple | None = None,
              max_rows: int | None = None) -> QueryResult:
    """Execute a query and return at most `max_rows` rows as a DataFrame.

    `params` are passed separately from the SQL text (use ? placeholders).
    Never build SQL by gluing user input into a string.
    """
    settings = get_settings()
    limit = max_rows or settings.max_rows
    start = time.perf_counter()

    conn = get_connection(settings)
    try:
        cursor = conn.cursor()
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)

        if cursor.description is None:
            raise DatabaseError("The statement did not return any rows to display.")

        columns = _clean_column_names([col[0] for col in cursor.description])
        # Fetch one extra row: if it exists, the result was cut off.
        rows = cursor.fetchmany(limit + 1)
    except pyodbc.Error as exc:
        elapsed = time.perf_counter() - start
        logger.warning("Query failed after %.2fs (SQLSTATE %s)", elapsed, _sqlstate(exc))
        raise DatabaseError(_friendly_message(exc)) from exc
    finally:
        conn.close()   # also cancels any rows we did not fetch

    truncated = len(rows) > limit
    rows = rows[:limit]

    df = pd.DataFrame.from_records([tuple(r) for r in rows], columns=columns)
    df = _normalise_types(df)

    elapsed = time.perf_counter() - start
    # Metadata only - no SQL text, no result values.
    logger.info("Query OK: %d row(s)%s in %.3fs",
                len(df), " (truncated)" if truncated else "", elapsed)
    return QueryResult(df, len(df), truncated, elapsed)


def _clean_column_names(names: list[str]) -> list[str]:
    """Give unnamed columns a name and make duplicates unique.

    LLM-generated SQL often forgets aliases ("SELECT COUNT(*) ...") or
    selects two columns with the same name (c.City, e.City). Streamlit
    cannot display a DataFrame with blank or duplicate column names.
    """
    cleaned, seen = [], {}
    for i, name in enumerate(names, start=1):
        base = name if name else f"Column{i}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        cleaned.append(base if count == 1 else f"{base}_{count}")
    return cleaned


def _normalise_types(df: pd.DataFrame) -> pd.DataFrame:
    """SQL Server DECIMAL/MONEY arrive as Python Decimal objects, which Pandas
    stores as generic 'object' columns. Convert them to numbers so sorting,
    maths and (in Phase 10) charts work."""
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, Decimal)).any():
            df[col] = pd.to_numeric(
                df[col].map(lambda v: float(v) if isinstance(v, Decimal) else v)
            )
    return df


# ---------------------------------------------------------------------------
# Status helpers (used by the Streamlit sidebar in Phase 3)
# ---------------------------------------------------------------------------

def test_connection() -> tuple[bool, str]:
    """Return (True, details) if the database is reachable, else (False, reason)."""
    try:
        result = run_query("SELECT DB_NAME() AS DatabaseName, SUSER_SNAME() AS LoginName")
        row = result.dataframe.iloc[0]
        return True, f"Connected to {row['DatabaseName']} as {row['LoginName']}"
    except Exception as exc:   # config or database problem
        return False, str(exc)


def get_tables() -> list[str]:
    """List the user tables in the database (schema.table)."""
    result = run_query(
        """
        SELECT TABLE_SCHEMA + '.' + TABLE_NAME AS TableName
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_NAME
        """
    )
    return result.dataframe["TableName"].tolist()


def check_permissions() -> dict:
    """Ask SQL Server what the current login is ALLOWED to do.

    HAS_PERMS_BY_NAME only checks permissions - it never runs a write,
    so this is completely safe to call.
    """
    result = run_query(
        """
        SELECT
            t.name AS TableName,
            HAS_PERMS_BY_NAME(QUOTENAME(s.name) + '.' + QUOTENAME(t.name), 'OBJECT', 'SELECT') AS CanSelect,
            HAS_PERMS_BY_NAME(QUOTENAME(s.name) + '.' + QUOTENAME(t.name), 'OBJECT', 'INSERT') AS CanInsert,
            HAS_PERMS_BY_NAME(QUOTENAME(s.name) + '.' + QUOTENAME(t.name), 'OBJECT', 'UPDATE') AS CanUpdate,
            HAS_PERMS_BY_NAME(QUOTENAME(s.name) + '.' + QUOTENAME(t.name), 'OBJECT', 'DELETE') AS CanDelete,
            HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CREATE TABLE')                           AS CanCreateTable
        FROM sys.tables AS t
        JOIN sys.schemas AS s ON s.schema_id = t.schema_id
        ORDER BY t.name
        """
    )
    df = result.dataframe
    can_write = bool(df[["CanInsert", "CanUpdate", "CanDelete", "CanCreateTable"]].to_numpy().any())
    can_read_all = bool(df["CanSelect"].all())
    return {
        "read_only": can_read_all and not can_write,
        "can_read_all_tables": can_read_all,
        "can_write": can_write,
        "details": df,
    }


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def _sqlstate(exc: pyodbc.Error) -> str:
    return exc.args[0] if exc.args else "unknown"


def _driver_text(exc: pyodbc.Error) -> str:
    """Strip the '[Microsoft][ODBC Driver 18 ...]' noise from driver messages."""
    text = str(exc.args[1]) if len(exc.args) > 1 else str(exc)
    text = re.sub(r"\[[^\]]*\]", "", text)                  # [Microsoft][ODBC ...]
    text = re.sub(r"\(\d+\)\s*\(SQL\w+\)", "", text)        # (208) (SQLExecDirectW)
    text = re.sub(r"\(SQL\w+\)", "", text)
    return " ".join(text.split()).strip(" ;")


# Common SQLSTATE codes -> hints a student can act on.
_CONNECTION_HINTS = {
    "IM002": "ODBC driver not found. Install 'ODBC Driver 18 for SQL Server' "
             "or fix DB_DRIVER in .env.",
    "28000": "Login failed. Check DB_USERNAME / DB_PASSWORD, and that SQL Server "
             "allows 'SQL Server and Windows Authentication mode'.",
    "08001": "Cannot reach SQL Server. Check DB_SERVER and that the SQL Server "
             "service is running.",
    "HYT00": "The operation timed out (see DB_QUERY_TIMEOUT / DB_LOGIN_TIMEOUT).",
}


def _friendly_message(exc: pyodbc.Error) -> str:
    state = _sqlstate(exc)
    if state in _CONNECTION_HINTS:
        return _CONNECTION_HINTS[state]
    # Query errors (invalid column, permission denied, syntax ...): the server's
    # own message is useful, and in Phase 5 the LLM can use it to fix its SQL.
    return f"SQL error ({state}): {_driver_text(exc)}"
