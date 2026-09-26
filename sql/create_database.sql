/* =====================================================================
   GenAI SQL Assistant - Phase 1
   File   : sql/create_database.sql
   Purpose: Create the fictional GenAI_Demo_DB database and its schema.

   Run this FIRST, then run data/sample_data.sql.

   WARNING: If GenAI_Demo_DB already exists, this script DROPS it and
   recreates it from scratch. That is intentional for a demo database
   (it lets you reset everything), but never do this on real data.
   ===================================================================== */

USE master;
GO

-- 1. Drop the old copy (if any) so the script can be re-run safely.
IF DB_ID(N'GenAI_Demo_DB') IS NOT NULL
BEGIN
    -- Kick out open connections (e.g. another SSMS query window).
    ALTER DATABASE GenAI_Demo_DB SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE GenAI_Demo_DB;
END
GO

-- 2. Create the database.
CREATE DATABASE GenAI_Demo_DB;
GO

USE GenAI_Demo_DB;
GO

/* ---------------------------------------------------------------------
   3. Tables
   Parent tables (Customers, Products) are created before the child
   tables (Orders, OrderDetails) that reference them.
   --------------------------------------------------------------------- */

CREATE TABLE dbo.Customers
(
    CustomerID       INT IDENTITY(1,1) NOT NULL,
    FirstName        NVARCHAR(50)      NOT NULL,
    LastName         NVARCHAR(50)      NOT NULL,
    Email            NVARCHAR(100)     NOT NULL,
    City             NVARCHAR(50)      NOT NULL,
    Country          NVARCHAR(50)      NOT NULL,
    RegistrationDate DATE              NOT NULL,

    CONSTRAINT PK_Customers        PRIMARY KEY (CustomerID),
    CONSTRAINT UQ_Customers_Email  UNIQUE (Email)
);

CREATE TABLE dbo.Products
(
    ProductID     INT IDENTITY(1,1) NOT NULL,
    ProductName   NVARCHAR(100)     NOT NULL,
    Category      NVARCHAR(50)      NOT NULL,
    Price         DECIMAL(10,2)     NOT NULL,
    StockQuantity INT               NOT NULL,

    CONSTRAINT PK_Products        PRIMARY KEY (ProductID),
    CONSTRAINT CK_Products_Price  CHECK (Price >= 0),
    CONSTRAINT CK_Products_Stock  CHECK (StockQuantity >= 0)
);

CREATE TABLE dbo.Orders
(
    OrderID     INT IDENTITY(1,1) NOT NULL,
    CustomerID  INT               NOT NULL,
    OrderDate   DATE              NOT NULL,
    Status      NVARCHAR(20)      NOT NULL,
    -- Stored total (sum of the order's lines). Kept for simple queries
    -- like "Show orders above 1000"; filled in by sample_data.sql.
    TotalAmount DECIMAL(12,2)     NOT NULL
        CONSTRAINT DF_Orders_TotalAmount DEFAULT (0),

    CONSTRAINT PK_Orders            PRIMARY KEY (OrderID),
    CONSTRAINT FK_Orders_Customers  FOREIGN KEY (CustomerID)
                                    REFERENCES dbo.Customers (CustomerID),
    CONSTRAINT CK_Orders_Status     CHECK (Status IN (N'Pending', N'Shipped',
                                                      N'Delivered', N'Cancelled')),
    CONSTRAINT CK_Orders_Total      CHECK (TotalAmount >= 0)
);

CREATE TABLE dbo.OrderDetails
(
    OrderDetailID INT IDENTITY(1,1) NOT NULL,
    OrderID       INT               NOT NULL,
    ProductID     INT               NOT NULL,
    Quantity      INT               NOT NULL,
    -- Price at the time of the order (product prices can change later).
    UnitPrice     DECIMAL(10,2)     NOT NULL,

    CONSTRAINT PK_OrderDetails              PRIMARY KEY (OrderDetailID),
    CONSTRAINT FK_OrderDetails_Orders       FOREIGN KEY (OrderID)
                                            REFERENCES dbo.Orders (OrderID),
    CONSTRAINT FK_OrderDetails_Products     FOREIGN KEY (ProductID)
                                            REFERENCES dbo.Products (ProductID),
    CONSTRAINT UQ_OrderDetails_OrderProduct UNIQUE (OrderID, ProductID),
    CONSTRAINT CK_OrderDetails_Quantity     CHECK (Quantity > 0),
    CONSTRAINT CK_OrderDetails_UnitPrice    CHECK (UnitPrice >= 0)
);

CREATE TABLE dbo.Employees
(
    EmployeeID INT IDENTITY(1,1) NOT NULL,
    FirstName  NVARCHAR(50)      NOT NULL,
    LastName   NVARCHAR(50)      NOT NULL,
    Department NVARCHAR(50)      NOT NULL,
    City       NVARCHAR(50)      NOT NULL,
    HireDate   DATE              NOT NULL,
    Salary     DECIMAL(10,2)     NOT NULL,

    CONSTRAINT PK_Employees        PRIMARY KEY (EmployeeID),
    CONSTRAINT CK_Employees_Salary CHECK (Salary > 0)
);
GO

/* ---------------------------------------------------------------------
   4. Indexes on columns the LLM's queries will filter / join on most.
   (Primary keys and UNIQUE constraints already create their own indexes.)
   --------------------------------------------------------------------- */
CREATE INDEX IX_Orders_CustomerID        ON dbo.Orders (CustomerID);
CREATE INDEX IX_Orders_OrderDate         ON dbo.Orders (OrderDate);
CREATE INDEX IX_OrderDetails_ProductID   ON dbo.OrderDetails (ProductID);
CREATE INDEX IX_Customers_City           ON dbo.Customers (City);
CREATE INDEX IX_Employees_Department     ON dbo.Employees (Department);
GO

PRINT 'GenAI_Demo_DB schema created. Now run data/sample_data.sql.';
GO
