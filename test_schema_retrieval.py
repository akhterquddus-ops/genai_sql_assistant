"""
test_schema_retrieval.py - Phase 9: does schema retrieval find the right tables?

Run from the project folder (Ollama running with the embedding model, database available):
    ollama pull nomic-embed-text
    python test_schema_retrieval.py

Our real database has only 5 tables, too few to show why RAG matters. So this
test adds 40 FICTIONAL tables that a real company might have (Payroll,
Invoices, ProductReviews ...), several of them deliberately similar to our
real tables. They exist only in memory, not in SQL Server.

For each question we check:
  recall   - were ALL tables needed for the SQL retrieved?  (missing = wrong SQL)
  size     - how many tables / tokens were sent instead of the full schema?
"""

import logging
import time

from config import get_retrieval_settings
from ollama_client import OllamaError
from schema import Column, DatabaseSchema, ForeignKey, Table, get_schema
from schema_retriever import TABLE_DESCRIPTIONS, SchemaIndex, estimate_tokens

logging.basicConfig(level=logging.ERROR)

# name: (description, columns, optional foreign key "Column->Table")
DECOY_TABLES = {
    "Invoices": ("Bills sent to business clients with amount due and due date.",
                 ["InvoiceID", "ClientName", "InvoiceDate", "DueDate", "AmountDue", "IsPaid"], None),
    "Payments": ("Money received from clients for invoices, by payment method.",
                 ["PaymentID", "InvoiceID", "PaymentDate", "Amount", "Method"], None),
    "Refunds": ("Money returned to clients after complaints.",
                ["RefundID", "RefundDate", "Amount", "Reason"], None),
    "Returns": ("Products sent back by customers after an order.",
                ["ReturnID", "OrderID", "ReturnDate", "Reason"], "OrderID->Orders"),
    "ProductReviews": ("Star ratings and comments written about products.",
                       ["ReviewID", "ProductID", "Rating", "Comment", "ReviewDate"], "ProductID->Products"),
    "ProductCategories": ("Hierarchy of product category codes for the website menu.",
                          ["CategoryCode", "ParentCode", "DisplayName", "SortOrder"], None),
    "PriceHistory": ("Past list prices and when they changed.",
                     ["PriceHistoryID", "ProductCode", "OldPrice", "NewPrice", "ChangedOn"], None),
    "Suppliers": ("Companies we buy stock from, with contact person and country.",
                  ["SupplierID", "SupplierName", "ContactPerson", "Country", "Phone"], None),
    "PurchaseOrders": ("Orders we place with suppliers to restock the warehouse.",
                       ["PurchaseOrderID", "SupplierID", "OrderedOn", "ExpectedOn", "TotalCost"], None),
    "Warehouses": ("Storage buildings and their capacity.",
                   ["WarehouseID", "WarehouseName", "City", "CapacityPallets"], None),
    "InventoryMovements": ("Stock moved in or out of a warehouse.",
                           ["MovementID", "WarehouseID", "ItemCode", "QuantityChange", "MovedOn"], None),
    "Shipments": ("Parcels sent to addresses with tracking numbers and carrier.",
                  ["ShipmentID", "TrackingNumber", "Carrier", "ShippedOn", "DeliveredOn"], None),
    "ShippingCarriers": ("Courier companies and their delivery rates.",
                         ["CarrierID", "CarrierName", "RatePerKg"], None),
    "CustomerAddresses": ("Delivery and billing addresses of customers.",
                          ["AddressID", "CustomerID", "Street", "PostalCode", "AddressType"],
                          "CustomerID->Customers"),
    "SupportTickets": ("Customer service requests and complaints with status and priority.",
                       ["TicketID", "OpenedOn", "Priority", "TicketStatus", "Subject"], None),
    "LoyaltyPoints": ("Reward points earned and redeemed by members.",
                      ["LoyaltyID", "MemberNumber", "PointsEarned", "PointsRedeemed"], None),
    "Coupons": ("Discount codes with percentage and validity dates.",
                ["CouponCode", "DiscountPercent", "ValidFrom", "ValidTo"], None),
    "Campaigns": ("Marketing campaigns with budget, channel and dates.",
                  ["CampaignID", "CampaignName", "Channel", "Budget", "StartDate", "EndDate"], None),
    "CampaignResponses": ("Clicks and sign-ups caused by marketing campaigns.",
                          ["ResponseID", "CampaignID", "ResponseType", "ResponseDate"], None),
    "WebsiteSessions": ("Visits to the online shop: pages viewed and duration.",
                        ["SessionID", "StartedAt", "PagesViewed", "DurationSeconds", "Device"], None),
    "Payroll": ("Monthly pay slips: gross salary, tax deductions and net pay.",
                ["PayrollID", "EmployeeID", "PayMonth", "GrossSalary", "TaxDeducted", "NetPay"],
                "EmployeeID->Employees"),
    "EmployeeLeave": ("Holidays and sick leave taken by staff.",
                      ["LeaveID", "StaffNumber", "LeaveType", "FromDate", "ToDate"], None),
    "PerformanceReviews": ("Yearly appraisal scores of staff members.",
                           ["ReviewID", "StaffNumber", "ReviewYear", "Score", "ReviewerName"], None),
    "Departments": ("Organisation units with manager and cost centre.",
                    ["DepartmentCode", "DepartmentName", "ManagerName", "CostCentre"], None),
    "JobPositions": ("Job titles, grades and salary bands.",
                     ["PositionID", "Title", "Grade", "MinSalary", "MaxSalary"], None),
    "TrainingCourses": ("Internal training sessions and attendees count.",
                        ["CourseID", "CourseName", "TrainerName", "SessionDate", "Attendees"], None),
    "Attendance": ("Daily check-in and check-out times of staff.",
                   ["AttendanceID", "StaffNumber", "WorkDate", "CheckIn", "CheckOut"], None),
    "Expenses": ("Business expenses claimed by staff (travel, meals).",
                 ["ExpenseID", "StaffNumber", "ExpenseDate", "Category", "Amount"], None),
    "Budgets": ("Planned yearly budget per cost centre.",
                ["BudgetID", "CostCentre", "BudgetYear", "PlannedAmount"], None),
    "GeneralLedger": ("Accounting journal entries: debit and credit per account.",
                      ["EntryID", "AccountCode", "EntryDate", "Debit", "Credit"], None),
    "TaxRates": ("Sales tax percentage per region.",
                 ["TaxRateID", "Region", "RatePercent"], None),
    "Currencies": ("Currency codes and symbols.",
                   ["CurrencyCode", "CurrencyName", "Symbol"], None),
    "ExchangeRates": ("Daily exchange rates between currencies.",
                      ["RateID", "FromCurrency", "ToCurrency", "Rate", "RateDate"], None),
    "Stores": ("Physical shops with city and opening date.",
               ["StoreID", "StoreName", "City", "OpenedOn", "SquareMeters"], None),
    "StoreVisits": ("Number of visitors counted at shop entrances per day.",
                    ["VisitID", "StoreID", "VisitDate", "VisitorCount"], None),
    "Promotions": ("Temporary price reductions on selected items.",
                   ["PromotionID", "PromotionName", "DiscountPercent", "StartsOn", "EndsOn"], None),
    "SalesTargets": ("Monthly sales goals for each sales region.",
                     ["TargetID", "Region", "TargetMonth", "TargetAmount"], None),
    "Vendors": ("Service providers such as cleaning and IT support contracts.",
                ["VendorID", "VendorName", "ServiceType", "ContractEnd"], None),
    "AuditLog": ("Technical log of changes made in the system.",
                 ["LogID", "ChangedAt", "ChangedBy", "TableName", "Action"], None),
    "AppUsers": ("Login accounts of the internal applications.",
                 ["UserID", "UserName", "LastLogin", "IsActive"], None),
}

# (question, acceptable table sets: at least one set must be fully retrieved)
QUESTIONS = [
    ("Show all customers from Islamabad.", [{"Customers"}]),
    ("How many customers are registered in Pakistan?", [{"Customers"}]),
    ("Show the 10 most expensive products.", [{"Products"}]),
    ("Which products have less than 10 items in stock?", [{"Products"}]),
    ("How many orders were placed last month?", [{"Orders"}]),
    ("What is the total sales amount?", [{"Orders"}, {"OrderDetails"}]),
    ("Show total sales by month.", [{"Orders"}, {"OrderDetails"}]),
    ("What are the top 5 products by sales?", [{"Products", "OrderDetails"}]),
    ("Which customers have placed the most orders?", [{"Customers", "Orders"}]),
    ("Show employees working in the Sales department.", [{"Employees"}]),
    ("What is the average employee salary?", [{"Employees"}]),
    ("Which city has the highest number of customers?", [{"Customers"}]),
    ("Show orders above 1000.", [{"Orders"}]),
    # needs VALUE LINKING ("Laptop Pro 14" is a product name) and JOIN expansion
    ("Which customers bought a Laptop Pro 14?", [{"Customers", "Orders", "OrderDetails", "Products"}]),
]


def build_enterprise_schema(real: DatabaseSchema) -> DatabaseSchema:
    tables = list(real.tables)
    fks = list(real.foreign_keys)
    for name, (_, columns, fk) in DECOY_TABLES.items():
        cols = [Column(c, "int" if c.endswith("ID") else "nvarchar(100)", is_primary_key=(i == 0))
                for i, c in enumerate(columns)]
        tables.append(Table(name, cols))
        if fk:
            col, ref = fk.split("->")
            fks.append(ForeignKey(name, col, ref, f"{ref[:-1]}ID"))
    return DatabaseSchema(tables, fks)


def main() -> None:
    settings = get_retrieval_settings()
    schema = build_enterprise_schema(get_schema())
    descriptions = {**TABLE_DESCRIPTIONS, **{n: d for n, (d, _, _) in DECOY_TABLES.items()}}
    full_tokens = estimate_tokens(schema.to_prompt_text())

    print(f"Embedding model: {settings.embedding_model} · top_k = {settings.top_k}")
    print(f"Simulated enterprise schema: {len(schema.tables)} tables, "
          f"~{full_tokens:,} tokens if sent in full\n")
    try:
        index = SchemaIndex(schema, settings.embedding_model, descriptions)
    except OllamaError as exc:
        print(f"FAIL: {exc}")
        return
    print(f"Index built in {index.index_seconds:.1f}s (one-time cost)\n")

    found, sent_tables, sent_tokens, times = 0, [], [], []
    for i, (question, acceptable) in enumerate(QUESTIONS, start=1):
        start = time.perf_counter()
        tables, scores, by_value, added = index.retrieve(question, settings.top_k)
        times.append(time.perf_counter() - start)
        ok = any(need <= set(tables) for need in acceptable)
        found += ok
        tokens = estimate_tokens(schema.to_prompt_text(only=set(tables)))
        sent_tables.append(len(tables))
        sent_tokens.append(tokens)

        top = sorted(scores, key=scores.get, reverse=True)[:5]
        print(f"Q{i}: {question}")
        print("   top 5: " + ", ".join(f"{t} {scores[t]:.2f}" for t in top))
        joins = (f"  (+{', '.join(by_value)} by value)" if by_value else "") + \
                (f"  (+{', '.join(added)} for JOINs)" if added else "")
        print(f"   sent:  {', '.join(tables)}{joins}  ~{tokens} tokens")
        if not ok:
            needed = " or ".join("{" + ", ".join(sorted(s)) + "}" for s in acceptable)
            print(f"   ❌ missing: needed {needed}")
        else:
            print("   ✅ all needed tables retrieved")
        print()

    n = len(QUESTIONS)
    print(f"RECALL: {found}/{n} questions had every needed table")
    print(f"Average tables sent: {sum(sent_tables) / n:.1f} of {len(schema.tables)}")
    print(f"Average schema tokens: ~{sum(sent_tokens) // n:,} instead of ~{full_tokens:,} "
          f"({100 * (1 - sum(sent_tokens) / n / full_tokens):.0f}% smaller)")
    print(f"Average retrieval time: {sum(times) / n:.2f}s")


if __name__ == "__main__":
    main()
