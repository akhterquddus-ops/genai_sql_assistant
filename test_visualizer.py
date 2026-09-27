"""
test_visualizer.py - Phase 10: does the app choose sensible charts?

Runs anywhere (no database, no Ollama needed):
    python test_visualizer.py
"""

import datetime as dt

import pandas as pd

from visualizer import bar_sort, suggest_chart

months = [dt.date(2025, m, 1) for m in range(8, 13)] + [dt.date(2026, m, 1) for m in range(1, 10)]

CASES = [
    ("Show total sales by month.",
     pd.DataFrame({"SalesMonth": months, "TotalSales": [7649.5 + i * 700 for i in range(14)]}),
     "line"),
    ("Monthly orders and sales (two measures)",
     pd.DataFrame({"SalesMonth": months, "OrderCount": range(14), "TotalSales": range(14)}),
     "line"),
    ("LLM wrote YEAR() and MONTH() as numbers",
     pd.DataFrame({"Year": [2026] * 6, "Month": range(1, 7), "TotalSales": [5, 7, 6, 8, 9, 7]}),
     "line"),
    ("Compare sales between January and February.",
     pd.DataFrame({"SalesMonth": [dt.date(2026, 1, 1), dt.date(2026, 2, 1)], "TotalSales": [12000.0, 9800.0]}),
     "bar"),
    ("What are the top 5 products by sales?",
     pd.DataFrame({"ProductName": ["Laptop Pro 14", "Smartphone X", "27-inch Monitor", "Standing Desk", "Office Chair"],
                   "TotalSales": [28750.0, 20677.0, 18560.0, 12420.0, 12180.0]}),
     "bar"),
    ("Show the number of employees in each department. (unsorted)",
     pd.DataFrame({"Department": ["HR", "Sales", "IT", "Finance"], "EmployeeCount": [2, 5, 4, 2]}),
     "bar"),
    ("Which customers have placed the most orders? (first + last name)",
     pd.DataFrame({"FirstName": [f"Name{i}" for i in range(30)], "LastName": ["Khan"] * 30,
                   "OrderCount": list(range(30, 0, -1))}),
     "bar"),
    ("How many customers are registered in Pakistan?",
     pd.DataFrame({"CustomerCount": [18]}),
     "metric"),
    ("Show all customers from Islamabad. (no numbers)",
     pd.DataFrame({"FirstName": ["Ayesha", "Bilal"], "LastName": ["Khan", "Ahmed"], "City": ["Islamabad"] * 2}),
     "none"),
    ("Only IDs and numbers, no labels",
     pd.DataFrame({"OrderID": [1, 2, 3], "TotalAmount": [100.0, 250.0, 80.0]}),
     "none"),
    ("Empty result",
     pd.DataFrame({"ProductName": [], "Price": []}),
     "none"),
]


def main() -> None:
    passed = 0
    for i, (question, df, expected) in enumerate(CASES, start=1):
        spec = suggest_chart(df)
        ok = spec.kind == expected
        passed += ok
        print(f"Q{i}: {question}")
        detail = f"{spec.kind.upper()}"
        if spec.kind in ("bar", "line"):
            detail += f"  x={spec.x}  measures={spec.measures}"
            if spec.kind == "bar":
                detail += f"  horizontal={spec.horizontal}  sort={bar_sort(spec.data, spec.measures[0])!r}"
            detail += f"  rows={len(spec.data)}"
        print(f"   {'✅' if ok else '❌ expected ' + expected.upper() + ', got'} {detail}")
        print(f"   reason: {spec.reason}" + (f" ({spec.note})" if spec.note else ""))
        print()
    print(f"SCORE: {passed}/{len(CASES)}")


if __name__ == "__main__":
    main()
