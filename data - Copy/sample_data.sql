/* =====================================================================
   GenAI SQL Assistant - Phase 1
   File   : data/sample_data.sql
   Purpose: Load FICTIONAL sample data into GenAI_Demo_DB.

   Run AFTER sql/create_database.sql.
   To reset the data, re-run create_database.sql and then this file.

   All names, emails and records are invented. Emails use the reserved
   example.com domain so they can never belong to a real person.

   Why are order dates relative to today?
   Questions like "How many orders were placed last month?" or "Give me a
   summary of this month's sales" must always return rows, whenever you
   run the demo. So orders are generated across the last ~14 months
   counting back from GETDATE(). The pattern is deterministic (no random
   numbers), so every student gets the same shape of data.
   ===================================================================== */

USE GenAI_Demo_DB;
GO

SET NOCOUNT ON;

-- Safety guard: the formulas below assume fresh tables whose IDs start at 1.
IF EXISTS (SELECT 1 FROM dbo.Customers)
BEGIN
    RAISERROR('Tables already contain data. Re-run sql/create_database.sql first.', 16, 1);
    RETURN;
END

BEGIN TRY
    BEGIN TRANSACTION;

    /* -----------------------------------------------------------------
       Customers (30) - 18 in Pakistan, 12 abroad. Islamabad has the most.
       ----------------------------------------------------------------- */
    INSERT INTO dbo.Customers (FirstName, LastName, Email, City, Country, RegistrationDate)
    VALUES
    (N'Ayesha',  N'Khan',     N'ayesha.khan@example.com',     N'Islamabad',  N'Pakistan',             '2023-01-12'),
    (N'Bilal',   N'Ahmed',    N'bilal.ahmed@example.com',     N'Islamabad',  N'Pakistan',             '2023-02-03'),
    (N'Sara',    N'Malik',    N'sara.malik@example.com',      N'Lahore',     N'Pakistan',             '2023-02-21'),
    (N'Usman',   N'Tariq',    N'usman.tariq@example.com',     N'Karachi',    N'Pakistan',             '2023-03-15'),
    (N'Hina',    N'Raza',     N'hina.raza@example.com',       N'Islamabad',  N'Pakistan',             '2023-04-02'),
    (N'Omar',    N'Farooq',   N'omar.farooq@example.com',     N'Rawalpindi', N'Pakistan',             '2023-04-28'),
    (N'Zainab',  N'Hussain',  N'zainab.hussain@example.com',  N'Lahore',     N'Pakistan',             '2023-05-19'),
    (N'Hamza',   N'Sheikh',   N'hamza.sheikh@example.com',    N'Karachi',    N'Pakistan',             '2023-06-07'),
    (N'Fatima',  N'Noor',     N'fatima.noor@example.com',     N'Peshawar',   N'Pakistan',             '2023-07-11'),
    (N'Ali',     N'Hassan',   N'ali.hassan@example.com',      N'Lahore',     N'Pakistan',             '2023-08-24'),
    (N'Maryam',  N'Iqbal',    N'maryam.iqbal@example.com',    N'Islamabad',  N'Pakistan',             '2023-09-05'),
    (N'Danish',  N'Qureshi',  N'danish.qureshi@example.com',  N'Multan',     N'Pakistan',             '2023-10-16'),
    (N'Noor',    N'Fatima',   N'noor.fatima@example.com',     N'Faisalabad', N'Pakistan',             '2023-11-02'),
    (N'Kamran',  N'Aziz',     N'kamran.aziz@example.com',     N'Karachi',    N'Pakistan',             '2023-12-09'),
    (N'Sana',    N'Javed',    N'sana.javed@example.com',      N'Rawalpindi', N'Pakistan',             '2024-01-18'),
    (N'Emma',    N'Clarke',   N'emma.clarke@example.com',     N'London',     N'United Kingdom',       '2024-02-06'),
    (N'James',   N'Wilson',   N'james.wilson@example.com',    N'Manchester', N'United Kingdom',       '2024-03-13'),
    (N'Olivia',  N'Brown',    N'olivia.brown@example.com',    N'Toronto',    N'Canada',               '2024-04-01'),
    (N'Liam',    N'Martin',   N'liam.martin@example.com',     N'Vancouver',  N'Canada',               '2024-05-22'),
    (N'Sophia',  N'Miller',   N'sophia.miller@example.com',   N'New York',   N'United States',        '2024-06-10'),
    (N'Noah',    N'Davis',    N'noah.davis@example.com',      N'Chicago',    N'United States',        '2024-07-29'),
    (N'Aisha',   N'Rahman',   N'aisha.rahman@example.com',    N'Dubai',      N'United Arab Emirates', '2024-08-14'),
    (N'Yusuf',   N'Karim',    N'yusuf.karim@example.com',     N'Abu Dhabi',  N'United Arab Emirates', '2024-09-03'),
    (N'Lena',    N'Schmidt',  N'lena.schmidt@example.com',    N'Berlin',     N'Germany',              '2024-10-17'),
    (N'Lucas',   N'Weber',    N'lucas.weber@example.com',     N'Munich',     N'Germany',              '2024-11-25'),
    (N'Mei',     N'Tanaka',   N'mei.tanaka@example.com',      N'Tokyo',      N'Japan',                '2024-12-08'),
    (N'Carlos',  N'Garcia',   N'carlos.garcia@example.com',   N'Madrid',     N'Spain',                '2025-01-20'),
    (N'Amna',    N'Siddiqui', N'amna.siddiqui@example.com',   N'Islamabad',  N'Pakistan',             '2025-02-14'),
    (N'Faisal',  N'Mehmood',  N'faisal.mehmood@example.com',  N'Quetta',     N'Pakistan',             '2025-03-30'),
    (N'Rabia',   N'Anwar',    N'rabia.anwar@example.com',     N'Karachi',    N'Pakistan',             '2025-05-12');

    /* -----------------------------------------------------------------
       Products (25) - 6 categories; several have StockQuantity < 10.
       ----------------------------------------------------------------- */
    INSERT INTO dbo.Products (ProductName, Category, Price, StockQuantity)
    VALUES
    (N'Laptop Pro 14',               N'Electronics',    1250.00,  15),
    (N'Wireless Mouse',              N'Electronics',      25.00, 150),
    (N'Mechanical Keyboard',         N'Electronics',      85.00,  60),
    (N'27-inch Monitor',             N'Electronics',     320.00,   8),
    (N'Noise-Cancelling Headphones', N'Electronics',     199.00,  35),
    (N'Smartphone X',                N'Electronics',     899.00,   5),
    (N'USB-C Hub',                   N'Electronics',      45.00,  90),
    (N'Tablet 10',                   N'Electronics',     450.00,  12),
    (N'Office Chair',                N'Furniture',       210.00,  20),
    (N'Standing Desk',               N'Furniture',       540.00,   4),
    (N'Bookshelf',                   N'Furniture',       130.00,   9),
    (N'Coffee Maker',                N'Home & Kitchen',   75.00,  40),
    (N'Blender',                     N'Home & Kitchen',   60.00,  25),
    (N'Air Fryer',                   N'Home & Kitchen',  120.00,   7),
    (N'Cookware Set',                N'Home & Kitchen',  150.00,  18),
    (N'Yoga Mat',                    N'Sports',           30.00,  80),
    (N'Dumbbell Set',                N'Sports',           95.00,   3),
    (N'Running Shoes',               N'Sports',          110.00,  45),
    (N'Cricket Bat',                 N'Sports',           70.00,  22),
    (N'Water Bottle',                N'Sports',           15.00, 200),
    (N'Notebook Pack',               N'Stationery',       12.00, 300),
    (N'Gel Pen Set',                 N'Stationery',        8.50, 250),
    (N'Desk Organizer',              N'Stationery',       22.00,   6),
    (N'Python Programming Guide',    N'Books',            40.00,  50),
    (N'Data Science Handbook',       N'Books',            55.00,   2);

    /* -----------------------------------------------------------------
       Employees (20) - 7 departments.
       ----------------------------------------------------------------- */
    INSERT INTO dbo.Employees (FirstName, LastName, Department, City, HireDate, Salary)
    VALUES
    (N'Imran',   N'Saeed',   N'Sales',      N'Islamabad',  '2019-03-15',  85000),
    (N'Nadia',   N'Aslam',   N'Sales',      N'Lahore',     '2020-07-01',  78000),
    (N'Tariq',   N'Mahmood', N'Sales',      N'Karachi',    '2018-11-20',  92000),
    (N'Saima',   N'Yousaf',  N'Sales',      N'Islamabad',  '2022-02-10',  65000),
    (N'Hira',    N'Saleem',  N'Sales',      N'Faisalabad', '2024-11-18',  62000),
    (N'Adeel',   N'Riaz',    N'Marketing',  N'Lahore',     '2021-05-17',  72000),
    (N'Mehwish', N'Ali',     N'Marketing',  N'Islamabad',  '2023-01-09',  60000),
    (N'Asad',    N'Nawaz',   N'IT',         N'Islamabad',  '2017-09-04', 120000),
    (N'Kiran',   N'Shah',    N'IT',         N'Rawalpindi', '2020-12-01', 105000),
    (N'Waqas',   N'Anjum',   N'IT',         N'Karachi',    '2022-08-22',  88000),
    (N'Farah',   N'Naz',     N'IT',         N'Lahore',     '2024-03-11',  70000),
    (N'Shahid',  N'Latif',   N'Finance',    N'Islamabad',  '2016-06-30', 115000),
    (N'Uzma',    N'Khalid',  N'Finance',    N'Karachi',    '2021-10-05',  82000),
    (N'Rizwan',  N'Akhtar',  N'HR',         N'Islamabad',  '2019-01-14',  76000),
    (N'Sadia',   N'Parveen', N'HR',         N'Lahore',     '2023-06-19',  58000),
    (N'Junaid',  N'Bashir',  N'Support',    N'Rawalpindi', '2022-04-25',  52000),
    (N'Areeba',  N'Zafar',   N'Support',    N'Islamabad',  '2024-09-02',  48000),
    (N'Naveed',  N'Ghani',   N'Support',    N'Karachi',    '2021-02-08',  55000),
    (N'Bushra',  N'Kazmi',   N'Operations', N'Islamabad',  '2018-05-07',  90000),
    (N'Zeeshan', N'Haider',  N'Operations', N'Peshawar',   '2023-10-30',  67000);

    /* -----------------------------------------------------------------
       Orders (300) - generated, spread over the last ~14 months.

       n = 1..300 comes from a "numbers" CTE.
       * CustomerID : spread across all 30 customers, but customers 1-6
                      order more often, so "top customers" has a clear answer.
       * OrderDate  : today minus 0..419 days, spread evenly.
       * Status     : last 5 days = Pending, last 12 days = Shipped,
                      every 12th older order = Cancelled, rest = Delivered.
       ----------------------------------------------------------------- */
    DECLARE @Today DATE = CAST(GETDATE() AS DATE);

    WITH Numbers AS
    (
        SELECT TOP (300) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS n
        FROM sys.all_objects
    ),
    Calc AS
    (
        SELECT n,
               (n * 13) % 37  AS c,          -- customer bucket 0..36
               (n * 37) % 420 AS daysAgo     -- 0..419
        FROM Numbers
    )
    INSERT INTO dbo.Orders (CustomerID, OrderDate, Status)
    SELECT
        CASE WHEN c < 30 THEN c + 1 ELSE (c % 6) + 1 END,
        DATEADD(DAY, -daysAgo, @Today),
        CASE
            WHEN daysAgo < 5  THEN N'Pending'
            WHEN daysAgo < 12 THEN N'Shipped'
            WHEN n % 12 = 0   THEN N'Cancelled'
            ELSE N'Delivered'
        END
    FROM Calc
    ORDER BY n;     -- guarantees OrderID 1..300 follow n

    /* -----------------------------------------------------------------
       OrderDetails - 1 to 3 different products per order.
       Expensive items (>= 400) are bought one at a time; others 1-4.
       UnitPrice is copied from the product's current price.
       ----------------------------------------------------------------- */
    INSERT INTO dbo.OrderDetails (OrderID, ProductID, Quantity, UnitPrice)
    SELECT
        o.OrderID,
        p.ProductID,
        CASE WHEN p.Price >= 400 THEN 1
             ELSE ((o.OrderID + l.LineNum) % 4) + 1 END,
        p.Price
    FROM dbo.Orders AS o
    CROSS JOIN (VALUES (1), (2), (3)) AS l(LineNum)
    JOIN dbo.Products AS p
        ON p.ProductID = ((o.OrderID * 11 + l.LineNum * 5) % 25) + 1
    WHERE l.LineNum <= (o.OrderID % 3) + 1;

    /* -----------------------------------------------------------------
       Fill Orders.TotalAmount from the order lines.
       ----------------------------------------------------------------- */
    UPDATE o
    SET    o.TotalAmount = d.Total
    FROM   dbo.Orders AS o
    JOIN  (SELECT OrderID, SUM(Quantity * UnitPrice) AS Total
           FROM dbo.OrderDetails
           GROUP BY OrderID) AS d
        ON d.OrderID = o.OrderID;

    COMMIT TRANSACTION;
    PRINT 'Sample data loaded successfully.';
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;   -- show the real error in the Messages tab
END CATCH;

-- Quick check: row counts per table.
SELECT 'Customers'    AS TableName, COUNT(*) AS RowCnt FROM dbo.Customers    UNION ALL
SELECT 'Products',                  COUNT(*)           FROM dbo.Products     UNION ALL
SELECT 'Orders',                    COUNT(*)           FROM dbo.Orders       UNION ALL
SELECT 'OrderDetails',              COUNT(*)           FROM dbo.OrderDetails UNION ALL
SELECT 'Employees',                 COUNT(*)           FROM dbo.Employees;
GO
