"""
test_sql_validator.py - Phase 6 demonstration: why startswith("SELECT") fails.

Run from the project folder (no database or Ollama needed):
    python test_sql_validator.py

Each case is checked twice:
  naive      : sql.strip().upper().startswith("SELECT")
  validator  : sql_validator.validate_sql(sql)
"""

import logging
import os

# The validator only needs the database NAME from .env, not a connection.
os.environ.setdefault("DB_SERVER", "localhost")
os.environ.setdefault("DB_DATABASE", "GenAI_Demo_DB")
os.environ.setdefault("DB_AUTH_MODE", "windows")

from sql_validator import validate_sql  # noqa: E402

# Keep the table readable: the validator's own log lines are hidden here.
logging.getLogger("sql_validator").setLevel(logging.ERROR)

# (description, sql, should_be_allowed)
CASES = [
    # --- Safe queries the validator must ALLOW ---------------------------------
    ("Simple SELECT",
     "SELECT FirstName, LastName FROM dbo.Customers WHERE City = N'Islamabad';", True),
    ("JOIN + GROUP BY + TOP (real LLM output)",
     "SELECT TOP 5 p.ProductName, SUM(od.Quantity * od.UnitPrice) AS TotalSales "
     "FROM dbo.OrderDetails od JOIN dbo.Products p ON p.ProductID = od.ProductID "
     "GROUP BY p.ProductName ORDER BY TotalSales DESC;", True),
    ("CTE: starts with WITH, not SELECT",
     "WITH m AS (SELECT DATEFROMPARTS(YEAR(OrderDate), MONTH(OrderDate), 1) AS SalesMonth, "
     "SUM(TotalAmount) AS Total FROM dbo.Orders GROUP BY DATEFROMPARTS(YEAR(OrderDate), MONTH(OrderDate), 1)) "
     "SELECT SalesMonth, Total FROM m ORDER BY SalesMonth;", True),
    ("Comment before SELECT",
     "-- top products\nSELECT TOP 3 ProductName FROM dbo.Products ORDER BY Price DESC", True),
    ("Dangerous words inside a string are just text",
     "SELECT ProductName FROM dbo.Products WHERE ProductName = N'DROP TABLE; DELETE'", True),
    ("Subquery with EXISTS",
     "SELECT COUNT(*) AS Cnt FROM dbo.Orders o WHERE EXISTS "
     "(SELECT 1 FROM dbo.OrderDetails d WHERE d.OrderID = o.OrderID AND d.Quantity > 3)", True),

    # --- Attacks and mistakes the validator must BLOCK ------------------------
    ("Second statement after ;",
     "SELECT 1 FROM dbo.Customers; DROP TABLE dbo.Customers", False),
    ("Second statement WITHOUT ; (valid T-SQL!)",
     "SELECT 1 FROM dbo.Customers DROP TABLE dbo.Customers", False),
    ("SELECT ... INTO creates a new table",
     "SELECT * INTO dbo.CustomerCopy FROM dbo.Customers", False),
    ("Run an operating-system command",
     "SELECT 1 FROM dbo.Customers; EXEC xp_cmdshell 'dir C:\\'", False),
    ("Read a file / another server",
     "SELECT * FROM OPENROWSET('SQLNCLI', 'Server=x;Trusted_Connection=yes;', 'SELECT 1')", False),
    ("Hang the database for an hour",
     "SELECT 1 FROM dbo.Customers WAITFOR DELAY '01:00:00'", False),
    ("Lock a whole table",
     "SELECT Salary FROM dbo.Employees WITH (TABLOCKX, HOLDLOCK)", False),
    ("Another database",
     "SELECT name FROM master.dbo.sysdatabases", False),
    ("System tables (list logins, tables ...)",
     "SELECT name FROM sys.sql_logins", False),
    ("Hallucinated table (caught BEFORE reaching SQL Server)",
     "SELECT InvoiceID FROM dbo.Invoices", False),
    ("Keyword hidden by a comment trick",
     "SELECT 1 FROM dbo.Customers /* hidden */; DELETE FROM dbo.Orders", False),
    ("Lower-case destructive statement",
     "delete from dbo.Orders", False),
    ("MySQL LIMIT (real LLM mistake from Phase 5)",
     "SELECT ProductName FROM dbo.Products ORDER BY Price DESC LIMIT 5", False),
    ("MySQL INTERVAL (real LLM mistake from Phase 5)",
     "SELECT COUNT(*) FROM dbo.Orders WHERE OrderDate >= GETDATE() - INTERVAL 1 MONTH", False),
]


def main() -> None:
    naive_wrong, validator_wrong = 0, 0
    print(f"{'Case':<52} {'naive':<9} {'validator':<10} reason")
    print("-" * 120)
    for description, sql, should_allow in CASES:
        naive = sql.strip().upper().startswith("SELECT")
        result = validate_sql(sql)
        naive_ok = naive == should_allow
        validator_ok = result.is_valid == should_allow
        naive_wrong += not naive_ok
        validator_wrong += not validator_ok

        def mark(allowed: bool, correct: bool) -> str:
            return ("allow" if allowed else "BLOCK") + (" ✅" if correct else " ❌")

        reason = "" if result.is_valid else result.reason
        print(f"{description:<52} {mark(naive, naive_ok):<9} "
              f"{mark(result.is_valid, validator_ok):<10} {reason}")

    print("-" * 120)
    print(f"startswith('SELECT') wrong: {naive_wrong}/{len(CASES)}")
    print(f"validator wrong:            {validator_wrong}/{len(CASES)}")


if __name__ == "__main__":
    main()
