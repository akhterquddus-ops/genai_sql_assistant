"""
sql_generator.py - Turns a question into SQL.

PHASE 3 VERSION: a placeholder with NO AI.
It only recognises a few example questions and returns SQL a human wrote.

Why build a fake first?
  * The Streamlit page can be built and tested end to end right now.
  * It shows exactly which job the LLM will take over: this function.
    In Phase 5 we replace the dictionary lookup with a call to the local
    model through Ollama. app.py will not need to change.

Compare the hand-written SQL below with what the LLM generates later.
That comparison is a good way to judge the model's quality.
"""

import re


class SQLGenerationError(Exception):
    """Raised when no SQL can be produced for a question."""


# Hand-written reference SQL for some of the project's example questions.
DEMO_QUERIES: dict[str, str] = {
    "Show all customers from Islamabad.": """
SELECT CustomerID, FirstName, LastName, Email, City
FROM dbo.Customers
WHERE City = N'Islamabad'
ORDER BY LastName, FirstName;""",

    "Show the 10 most expensive products.": """
SELECT TOP 10 ProductName, Category, Price
FROM dbo.Products
ORDER BY Price DESC;""",

    "Which products have less than 10 items in stock?": """
SELECT ProductName, Category, StockQuantity
FROM dbo.Products
WHERE StockQuantity < 10
ORDER BY StockQuantity;""",

    "What are the top 5 products by sales?": """
SELECT TOP 5 p.ProductName,
       SUM(od.Quantity * od.UnitPrice) AS TotalSales
FROM dbo.OrderDetails AS od
JOIN dbo.Products AS p ON p.ProductID = od.ProductID
JOIN dbo.Orders   AS o ON o.OrderID   = od.OrderID
WHERE o.Status <> N'Cancelled'
GROUP BY p.ProductName
ORDER BY TotalSales DESC;""",

    "How many orders were placed last month?": """
SELECT COUNT(*) AS OrdersLastMonth
FROM dbo.Orders
WHERE OrderDate >= DATEADD(MONTH, -1, DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1))
  AND OrderDate <  DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1);""",

    "Show total sales by month.": """
SELECT DATEFROMPARTS(YEAR(OrderDate), MONTH(OrderDate), 1) AS SalesMonth,
       COUNT(*)         AS OrderCount,
       SUM(TotalAmount) AS TotalSales
FROM dbo.Orders
WHERE Status <> N'Cancelled'
GROUP BY DATEFROMPARTS(YEAR(OrderDate), MONTH(OrderDate), 1)
ORDER BY SalesMonth;""",

    "Show the number of employees in each department.": """
SELECT Department, COUNT(*) AS EmployeeCount
FROM dbo.Employees
GROUP BY Department
ORDER BY EmployeeCount DESC;""",
}

EXAMPLE_QUESTIONS: list[str] = list(DEMO_QUERIES)


def _normalise(text: str) -> str:
    """Lower-case, trim and drop punctuation so small typing differences match."""
    text = re.sub(r"[^\w\s]", "", text.lower())
    return " ".join(text.split())


_LOOKUP = {_normalise(q): sql.strip() for q, sql in DEMO_QUERIES.items()}


def generate_sql(question: str) -> str:
    """Return SQL for `question` (Phase 3: example questions only)."""
    sql = _LOOKUP.get(_normalise(question))
    if sql is None:
        raise SQLGenerationError(
            "Phase 3 has no AI yet, so only the example questions work. "
            "The local LLM will answer any question from Phase 5."
        )
    return sql
