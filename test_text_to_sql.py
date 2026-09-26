"""
test_text_to_sql.py - Phase 5 evaluation: how good is the local model?

Run from the project folder (Ollama running, read-only login in .env):
    python test_text_to_sql.py

For each test question we:
  1. ask the local LLM for SQL
  2. run the LLM's SQL AND a hand-written reference SQL
  3. compare the RESULTS (not the SQL text)

Why compare results? The same answer can be written in many ways:
"SELECT COUNT(*) ..." and "SELECT COUNT(OrderID) ..." look different but
return the same number. Researchers call this "execution accuracy".

We also check that the model refuses questions it cannot answer.
"""

import datetime as dt
import logging
from decimal import Decimal

import pandas as pd

from database import DatabaseError, check_permissions, run_query
from ollama_client import get_status
from sql_generator import SQLGenerationError, generate_sql
from sql_validator import validate_sql

logging.basicConfig(level=logging.ERROR)

# Hand-written reference answers (the "gold standard").
# They select only the ESSENTIAL columns: the model may add extra columns
# (e.g. City), but its rows must contain these values.
REFERENCE_SQL: dict[str, str] = {
    "Show all customers from Islamabad.": """
        SELECT FirstName, LastName FROM dbo.Customers WHERE City = N'Islamabad'""",
    "How many customers are registered in Pakistan?": """
        SELECT COUNT(*) FROM dbo.Customers WHERE Country = N'Pakistan'""",
    "Show the 10 most expensive products.": """
        SELECT TOP 10 ProductName FROM dbo.Products ORDER BY Price DESC""",
    "Which products have less than 10 items in stock?": """
        SELECT ProductName FROM dbo.Products WHERE StockQuantity < 10""",
    "How many orders were placed last month?": """
        SELECT COUNT(*) FROM dbo.Orders
        WHERE OrderDate >= DATEADD(MONTH, -1, DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1))
          AND OrderDate <  DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1)""",
    "What are the top 5 products by sales?": """
        SELECT TOP 5 p.ProductName, SUM(od.Quantity * od.UnitPrice)
        FROM dbo.OrderDetails od
        JOIN dbo.Products p ON p.ProductID = od.ProductID
        JOIN dbo.Orders o   ON o.OrderID = od.OrderID
        WHERE o.Status <> N'Cancelled'
        GROUP BY p.ProductName ORDER BY 2 DESC""",
    "Show the number of employees in each department.": """
        SELECT Department, COUNT(*) FROM dbo.Employees GROUP BY Department""",
    "What is the average employee salary?": """
        SELECT AVG(Salary) FROM dbo.Employees""",
    # Generalisation checks: these patterns (TOP, relative dates) are taught
    # by the prompt's examples, but these exact questions are NOT in the prompt.
    "Show the 3 most recently hired employees.": """
        SELECT TOP 3 FirstName, LastName FROM dbo.Employees ORDER BY HireDate DESC""",
    "How many orders were placed this month?": """
        SELECT COUNT(*) FROM dbo.Orders
        WHERE OrderDate >= DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1)
          AND OrderDate <  DATEADD(MONTH, 1, DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1))""",
}

# Questions the model should NOT answer with SQL.
EXPECTED_REFUSALS: dict[str, str] = {
    "Show each customer's phone number.": "unanswerable",
    "Show me everything.": "clarify",
}


# ---------------------------------------------------------------------------
# Comparing results
# ---------------------------------------------------------------------------

def _normalise(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (float, Decimal)):
        return round(float(value), 2)
    if isinstance(value, (dt.datetime, pd.Timestamp)):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if hasattr(value, "item"):                 # numpy numbers -> Python numbers
        return _normalise(value.item())
    return str(value).strip()


def _row_set(df: pd.DataFrame) -> list:
    """Rows as comparable values, ignoring column names, column order and row order."""
    rows = [sorted((repr(_normalise(v)) for v in row)) for row in df.itertuples(index=False)]
    return sorted(rows)


def compare(model_df: pd.DataFrame, reference_df: pd.DataFrame) -> tuple[bool, str]:
    if len(model_df) != len(reference_df):
        return False, f"row count differs ({len(model_df)} vs {len(reference_df)})"
    if _row_set(model_df) == _row_set(reference_df):
        return True, "same results"
    # The model may add or drop harmless extra columns: check the reference's
    # values are contained in the model's rows.
    model_rows = [set(r) for r in _row_set(model_df)]
    ref_rows = [set(r) for r in _row_set(reference_df)]
    if all(any(ref <= m for m in model_rows) for ref in ref_rows):
        return True, "same data (different columns)"
    return False, "different values"


# ---------------------------------------------------------------------------

def main() -> None:
    status = get_status()
    if not (status.running and status.model_available):
        print(f"FAIL: {status.message}")
        return
    if not check_permissions()["read_only"]:
        print("FAIL: this test runs model-written SQL. Use the read-only genai_reader login.")
        return

    print(f"Model: {status.model}\n")
    passed, total, times = 0, 0, []

    for question, reference in REFERENCE_SQL.items():
        total += 1
        print(f"Q{total}: {question}")
        try:
            gen = generate_sql(question)
        except SQLGenerationError as exc:
            print(f"   ❌ generation failed: {exc}\n")
            continue
        times.append(gen.elapsed_seconds)
        if gen.status != "ok":
            print(f"   ❌ model answered '{gen.status}': {gen.message}\n")
            continue

        print("   SQL: " + " ".join(gen.sql.split()))
        validation = validate_sql(gen.sql)           # never run unchecked LLM SQL
        if not validation.is_valid:
            print(f"   ❌ blocked by validator: {validation.reason}  ({gen.elapsed_seconds:.1f}s)\n")
            continue
        try:
            model_df = run_query(validation.sql).dataframe
        except DatabaseError as exc:
            print(f"   ❌ model SQL failed: {exc}  ({gen.elapsed_seconds:.1f}s)\n")
            continue
        reference_df = run_query(reference).dataframe
        ok, reason = compare(model_df, reference_df)
        passed += ok
        print(f"   {'✅' if ok else '❌'} {reason}  ({gen.elapsed_seconds:.1f}s)\n")

    for question, expected in EXPECTED_REFUSALS.items():
        total += 1
        print(f"Q{total}: {question}   (expected: {expected})")
        try:
            gen = generate_sql(question)
        except SQLGenerationError as exc:
            print(f"   ❌ generation failed: {exc}\n")
            continue
        times.append(gen.elapsed_seconds)
        if gen.status == "ok":
            print(f"   ❌ model wrote SQL instead of refusing: {' '.join(gen.sql.split())}\n")
        else:
            # "clarify" vs "unanswerable" are both acceptable refusals.
            passed += 1
            print(f"   ✅ {gen.status}: {gen.message}  ({gen.elapsed_seconds:.1f}s)\n")

    avg = sum(times) / len(times) if times else 0
    print(f"SCORE: {passed}/{total} correct · average generation time {avg:.1f}s")


if __name__ == "__main__":
    main()
