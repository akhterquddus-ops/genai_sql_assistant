"""
sql_validator.py - Decide whether LLM-generated SQL is safe to run.

    question -> LLM -> SQL -> [ VALIDATOR ] -> SQL Server
                                  |
                          rejected SQL never reaches the database

Why not just   sql.strip().upper().startswith("SELECT")  ?
  * "SELECT 1; DROP TABLE dbo.Customers"        starts with SELECT
  * "SELECT 1 DROP TABLE dbo.Customers"         T-SQL needs no semicolon!
  * "SELECT * INTO dbo.Copy FROM dbo.Customers" a SELECT that CREATES a table
  * "SELECT * FROM OPENROWSET(...)"             reads files / other servers
  * "SELECT 1 WAITFOR DELAY '01:00:00'"         a SELECT that hangs for an hour
  * "SELECT * FROM master.sys.sql_logins"       another database / system data
All of these pass the startswith() test. And it also REJECTS safe SQL:
  * "WITH totals AS (...) SELECT ..."           a normal read-only query
  * "-- top products\nSELECT ..."               a comment first

So we check the SQL in two independent layers:

  Layer 1 - lexical scan (our own code, easy to read):
      remove comments, blank out string contents, then look at the words
      that are really SQL code: one statement only, starts with
      SELECT/WITH, no dangerous keywords, no MySQL-only syntax.

  Layer 2 - real SQL parser (sqlglot, T-SQL dialect):
      builds a syntax tree, so it knows exactly which tables are read,
      even inside subqueries and CTEs. Only APPROVED_TABLES are allowed.

Anything we cannot analyse is rejected ("fail closed").

This is APPLICATION-LEVEL security. It works together with the
DATABASE-LEVEL security of the read-only login (Phase 1-2): if the
validator ever misses something, SQL Server itself still refuses writes.
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from config import get_settings
from schema import APPROVED_TABLES

logger = logging.getLogger(__name__)

MAX_SQL_LENGTH = 5000     # characters; LLM answers are far shorter

# Words that must never appear as SQL code (outside strings and comments).
FORBIDDEN_KEYWORDS = {
    # change data or structure
    "INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "ALTER", "CREATE",
    "TRUNCATE", "INTO",
    # run code / change permissions / server administration
    "EXEC", "EXECUTE", "GRANT", "REVOKE", "DENY", "BACKUP", "RESTORE",
    "SHUTDOWN", "KILL", "DBCC", "RECONFIGURE", "BULK",
    # batch control: switch database, variables, session settings
    "USE", "DECLARE", "SET", "GO",
    # reach outside the database (files, other servers)
    "OPENROWSET", "OPENQUERY", "OPENDATASOURCE", "OPENXML",
    # denial of service: hang or lock the database
    "WAITFOR", "TABLOCK", "TABLOCKX", "XLOCK", "UPDLOCK", "HOLDLOCK",
}

# Syntax from other databases that small models often produce.
DIALECT_HINTS = {
    "LIMIT": "LIMIT is MySQL/PostgreSQL syntax. SQL Server uses SELECT TOP n.",
    "INTERVAL": "INTERVAL is MySQL/PostgreSQL syntax. SQL Server uses DATEADD().",
    "ILIKE": "ILIKE is PostgreSQL syntax. SQL Server uses LIKE.",
}


class ValidationError(Exception):
    """Raised when SQL must not be executed."""

    def __init__(self, message: str, rule: str = "invalid"):
        super().__init__(message)
        self.rule = rule          # short name for logs, e.g. "forbidden_keyword"


@dataclass
class ValidationResult:
    is_valid: bool
    sql: str                              # cleaned SQL to execute (if valid)
    reason: str = ""                      # why it was rejected
    rule: str = ""                        # short rule name, for logs and statistics
    tables: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)   # checks that passed


# ---------------------------------------------------------------------------
# Layer 1: lexical scan
# ---------------------------------------------------------------------------

def strip_comments_and_strings(sql: str) -> str:
    """Return the SQL with comments removed and string contents blanked.

    'DROP TABLE' inside a string is just text, and a comment can hide or
    split keywords. After this step, every word left is real SQL code.
    Handles: -- comments, nested /* */ comments (T-SQL allows nesting),
    'strings' with '' escapes, N'unicode strings', [bracket identifiers]
    and "quoted identifiers" (identifiers are kept, as [name]).
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch, nxt = sql[i], sql[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":                      # line comment
            end = sql.find("\n", i)
            i = n if end == -1 else end
            out.append(" ")
        elif ch == "/" and nxt == "*":                    # block comment (nestable)
            depth, i = 1, i + 2
            while i < n and depth:
                if sql.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif sql.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                raise ValidationError("Unterminated /* comment.", "syntax")
            out.append(" ")
        elif ch == "'":                                   # string literal
            i += 1
            while True:
                if i >= n:
                    raise ValidationError("Unterminated string literal.", "syntax")
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":   # '' is an escaped quote
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append("''")                              # keep an empty string
        elif ch in "[\"":                                 # quoted identifier
            close = "]" if ch == "[" else '"'
            j = i + 1
            while True:
                if j >= n:
                    raise ValidationError("Unterminated quoted identifier.", "syntax")
                if sql[j] == close:
                    if j + 1 < n and sql[j + 1] == close:
                        j += 2
                        continue
                    break
                j += 1
            out.append("[" + sql[i + 1:j].replace("]]", "]") + "]")
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


_WORD = re.compile(r"\[[^\]]*\]|[A-Za-z_@#][A-Za-z0-9_@#$]*")


def _code_words(code: str) -> list[str]:
    """Upper-case keywords and names; [bracketed] identifiers are skipped."""
    return [w.upper() for w in _WORD.findall(code) if not w.startswith("[")]


def _lexical_checks(sql: str, checks: list[str]) -> str:
    code = strip_comments_and_strings(sql)

    # One statement only. A single trailing semicolon is fine.
    body = code.strip().rstrip(";").strip()
    if not body:
        raise ValidationError("The SQL is empty.", "empty")
    if ";" in body:
        raise ValidationError("Multiple SQL statements are not allowed.", "multiple_statements")
    checks.append("Single statement")

    words = _code_words(body)
    if words[0] not in ("SELECT", "WITH"):
        raise ValidationError(f"Only SELECT queries are allowed (found '{words[0]}').", "not_select")
    checks.append("Starts with SELECT / WITH")

    for word in words:
        if word in FORBIDDEN_KEYWORDS:
            raise ValidationError(f"Forbidden keyword: {word}.", "forbidden_keyword")
        if word.startswith(("XP_", "SP_")):
            raise ValidationError(f"Calling system procedures is not allowed ({word.lower()}).", "system_procedure")
    checks.append("No forbidden keywords")

    if "`" in body:
        raise ValidationError("Backtick quotes are MySQL syntax. SQL Server uses [brackets].", "wrong_dialect")
    for word in words:
        if word in DIALECT_HINTS:
            raise ValidationError(DIALECT_HINTS[word], "wrong_dialect")
    checks.append("SQL Server (T-SQL) syntax")
    return body


# ---------------------------------------------------------------------------
# Layer 2: real SQL parser
# ---------------------------------------------------------------------------

_WRITE_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop,
                exp.Create, exp.Alter, exp.Command, exp.Into)


def _parser_checks(sql: str, checks: list[str]) -> list[str]:
    try:
        statements = [s for s in sqlglot.parse(sql, read="tsql") if s is not None]
    except sqlglot.errors.ParseError:
        raise ValidationError("The SQL could not be parsed, so it cannot be checked safely.", "parse_error")
    if len(statements) != 1:
        raise ValidationError("Multiple SQL statements are not allowed.", "multiple_statements")
    tree = statements[0]

    if not isinstance(tree, exp.Query):                   # SELECT / UNION / ...
        raise ValidationError("Only SELECT queries are allowed.", "not_select")
    if tree.find(*_WRITE_NODES) or any(s.args.get("into") for s in tree.find_all(exp.Select)):
        raise ValidationError("The query tries to write or create data.", "write_operation")
    checks.append("Parsed as a read-only query")

    cte_names = {cte.alias.upper() for cte in tree.find_all(exp.CTE)}
    approved = {t.upper(): t for t in APPROVED_TABLES}
    database = get_settings().database.upper()
    used: set[str] = set()

    for table in tree.find_all(exp.Table):
        name, schema_name, catalog = table.name, table.db, table.catalog
        if not name:
            raise ValidationError("Table functions (e.g. OPENROWSET, STRING_SPLIT) are not allowed.", "table_function")
        if not schema_name and name.upper() in cte_names:
            continue                                      # a CTE defined in this query
        if catalog and catalog.upper() != database:
            raise ValidationError(f"Access to another database is not allowed ({catalog}).", "other_database")
        if schema_name and schema_name.upper() != "DBO":
            raise ValidationError(f"Schema '{schema_name}' is not allowed (only dbo).", "schema_not_allowed")
        if name.upper() not in approved:
            allowed = ", ".join(APPROVED_TABLES)
            raise ValidationError(f"Table '{name}' is not allowed. Allowed tables: {allowed}.", "table_not_allowed")
        used.add(approved[name.upper()])

    if not used:
        raise ValidationError("The query does not read from any approved table.", "no_table")
    checks.append("Approved tables only")
    return sorted(used)


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def validate_sql(sql: str) -> ValidationResult:
    """Check SQL before execution. Never raises: returns is_valid True/False."""
    checks: list[str] = []
    sql = (sql or "").strip()
    try:
        if len(sql) > MAX_SQL_LENGTH:
            raise ValidationError(f"The SQL is too long (over {MAX_SQL_LENGTH} characters).", "too_long")
        clean = _lexical_checks(sql, checks)
        tables = _parser_checks(sql, checks)
    except ValidationError as exc:
        result = ValidationResult(False, sql, reason=str(exc), rule=exc.rule, checks=checks)
    except Exception as exc:                              # anything unexpected: fail closed
        logger.exception("Validator error")
        result = ValidationResult(False, sql, reason=f"The SQL could not be checked safely ({type(exc).__name__}).",
                                  rule="internal_error", checks=checks)
    else:
        result = ValidationResult(True, sql.rstrip().rstrip(";"), tables=tables, checks=checks)

    _log(sql, result)
    return result


def _log(sql: str, result: ValidationResult) -> None:
    """Log metadata only. The SQL text can contain names or values from the
    user's question, so we log a short fingerprint (hash) instead."""
    fingerprint = hashlib.sha256(sql.encode("utf-8")).hexdigest()[:12]
    if result.is_valid:
        logger.info("SQL accepted [%s] len=%d tables=%s", fingerprint, len(sql), ",".join(result.tables))
    else:
        logger.warning("SQL rejected [%s] len=%d rule=%s", fingerprint, len(sql), result.rule)
