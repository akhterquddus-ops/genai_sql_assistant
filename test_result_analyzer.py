"""
test_result_analyzer.py - Phase 7 check: can the local LLM explain results honestly?

Run from the project folder (Ollama running, database available):
    python test_result_analyzer.py

For each question we run a known-good SQL query, ask the model to explain the
results, and check whether the explanation contains numbers that are NOT in
the data (a sign of hallucination or bad arithmetic).
"""

import logging

from database import run_query
from ollama_client import get_status
from result_analyzer import ExplanationError, build_facts, explain

logging.basicConfig(level=logging.ERROR)

CASES = [
    ("What are the top 5 products by sales?",
     """SELECT TOP 5 p.ProductName, SUM(od.Quantity * od.UnitPrice) AS TotalSales
        FROM dbo.OrderDetails od JOIN dbo.Products p ON p.ProductID = od.ProductID
        JOIN dbo.Orders o ON o.OrderID = od.OrderID
        WHERE o.Status <> N'Cancelled'
        GROUP BY p.ProductName ORDER BY TotalSales DESC"""),
    ("How many customers are registered in Pakistan?",
     "SELECT COUNT(*) AS CustomerCount FROM dbo.Customers WHERE Country = N'Pakistan'"),
    ("Show the number of employees in each department.",
     "SELECT Department, COUNT(*) AS EmployeeCount FROM dbo.Employees GROUP BY Department ORDER BY EmployeeCount DESC"),
    ("Show total sales by month.",
     """SELECT DATEFROMPARTS(YEAR(OrderDate), MONTH(OrderDate), 1) AS SalesMonth,
               SUM(TotalAmount) AS TotalSales
        FROM dbo.Orders WHERE Status <> N'Cancelled'
        GROUP BY DATEFROMPARTS(YEAR(OrderDate), MONTH(OrderDate), 1) ORDER BY SalesMonth"""),
    ("Show all customers from Islamabad.",     # includes Email: must be hidden
     "SELECT FirstName, LastName, Email, City FROM dbo.Customers WHERE City = N'Islamabad'"),
    ("Show customers from Atlantis.",          # no rows: no LLM call needed
     "SELECT FirstName, LastName FROM dbo.Customers WHERE City = N'Atlantis'"),
]


def main() -> None:
    status = get_status()
    if not (status.running and status.model_available):
        print(f"FAIL: {status.message}")
        return
    print(f"Model: {status.model}\n")

    flagged = 0
    for i, (question, sql) in enumerate(CASES, start=1):
        result = run_query(sql)
        print(f"Q{i}: {question}  ({result.row_count} rows)")
        if i == 1:
            facts, _ = build_facts(result.dataframe, result.truncated)
            print("   --- facts given to the model (first question only) ---")
            print("   " + facts.replace("\n", "\n   "))
            print("   -------------------------------------------------------")
        try:
            exp = explain(question, result.dataframe, result.truncated)
        except ExplanationError as exc:
            print(f"   FAIL: {exc}\n")
            continue
        print("   " + exp.text.replace("\n", "\n   "))
        if exp.hidden_columns:
            print(f"   🔒 hidden from the model: {', '.join(exp.hidden_columns)}")
        if exp.unverified_numbers:
            flagged += 1
            print(f"   ⚠️  numbers not found in the data: {', '.join(exp.unverified_numbers)}")
        else:
            print("   ✅ every number in the explanation appears in the data")
        source = f"{exp.seconds:.1f}s" if exp.used_llm else "no LLM call (empty result)"
        print(f"   ({source})\n")

    print(f"Explanations with unverified numbers: {flagged}/{len(CASES)}")


if __name__ == "__main__":
    main()
