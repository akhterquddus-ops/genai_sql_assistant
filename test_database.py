"""
test_database.py - Phase 2 check: Python -> SQL Server -> SELECT -> Pandas

Run from the project folder:
    python test_database.py

Each step prints PASS / FAIL / WARN so you can see exactly what works.
"""

import logging

import pandas as pd

from config import ConfigError, get_settings
from database import (DatabaseError, check_permissions, get_tables,
                      run_query, test_connection)

logging.basicConfig(level=logging.INFO, format="   [log] %(levelname)s %(message)s")
pd.set_option("display.width", 120)


def step(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    # 1. Configuration --------------------------------------------------
    step("1. Load settings from .env")
    try:
        settings = get_settings()
    except ConfigError as exc:
        print(f"FAIL: {exc}")
        return
    print(f"PASS: {settings}")          # note: the password is NOT printed

    # 2. Connection -----------------------------------------------------
    step("2. Connect to SQL Server")
    ok, message = test_connection()
    print(("PASS: " if ok else "FAIL: ") + message)
    if not ok:
        return

    # 3. Tables ---------------------------------------------------------
    step("3. List tables")
    tables = get_tables()
    print(f"PASS: {len(tables)} tables -> {', '.join(tables)}")

    # 4. A real query into a DataFrame -----------------------------------
    step("4. SELECT into a Pandas DataFrame: customers from Islamabad")
    result = run_query(
        "SELECT CustomerID, FirstName, LastName, City "
        "FROM dbo.Customers WHERE City = ? ORDER BY CustomerID",
        params=["Islamabad"],          # parameter, not string concatenation
    )
    print(result.dataframe.to_string(index=False))
    print(f"PASS: {result.row_count} rows in {result.elapsed_seconds:.3f}s")

    # 5. Aggregation + numeric types --------------------------------------
    step("5. Top 5 products by sales (DECIMAL -> float conversion)")
    result = run_query(
        """
        SELECT TOP 5 p.ProductName, SUM(od.Quantity * od.UnitPrice) AS TotalSales
        FROM dbo.OrderDetails AS od
        JOIN dbo.Products AS p ON p.ProductID = od.ProductID
        GROUP BY p.ProductName
        ORDER BY TotalSales DESC
        """
    )
    print(result.dataframe.to_string(index=False))
    print(f"PASS: TotalSales dtype = {result.dataframe['TotalSales'].dtype}")

    # 6. Row limit --------------------------------------------------------
    step("6. Row limit: ask for all 300 orders, allow only 5")
    result = run_query("SELECT OrderID, OrderDate, TotalAmount FROM dbo.Orders", max_rows=5)
    print(f"{'PASS' if result.truncated and result.row_count == 5 else 'FAIL'}: "
          f"returned {result.row_count} rows, truncated={result.truncated}")

    # 7. Unnamed column ---------------------------------------------------
    step("7. Unnamed column (typical of LLM-written SQL)")
    result = run_query("SELECT COUNT(*) FROM dbo.Orders")
    print(f"PASS: column name = {list(result.dataframe.columns)}, "
          f"value = {result.dataframe.iloc[0, 0]}")

    # 8. Error handling ---------------------------------------------------
    step("8. Error handling: a table that does not exist (a 'hallucinated' table)")
    try:
        run_query("SELECT * FROM dbo.Invoices")
        print("FAIL: expected an error")
    except DatabaseError as exc:
        print(f"PASS: clean error -> {exc}")

    # 9. Read-only check ------------------------------------------------
    step("9. Database-level security: is this login read-only?")
    perms = check_permissions()
    if perms["read_only"]:
        print("PASS: login can read every table and cannot write anything.")
    else:
        print("WARN: this login CAN modify data. Fine for a first test, but switch "
              "to the read-only 'genai_reader' login before connecting the LLM.")
        print(perms["details"].to_string(index=False))

    print("\nPhase 2 complete.")


if __name__ == "__main__":
    main()
