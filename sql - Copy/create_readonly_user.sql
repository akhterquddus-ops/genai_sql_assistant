/* =====================================================================
   GenAI SQL Assistant - Phase 1 (security)
   File   : sql/create_readonly_user.sql
   Purpose: Create a READ-ONLY login that the Streamlit app will use.

   Why? Even with a Python SQL validator, the database itself should
   refuse writes. If the validator ever has a bug, this account still
   cannot INSERT, UPDATE, DELETE, DROP or ALTER anything.

   Before running:
   1. Replace the password below with your own strong password.
      Do not commit your real password to Git; it belongs only in .env.
   2. SQL Server must allow "SQL Server and Windows Authentication mode"
      (see the setup notes that come with these scripts).
   ===================================================================== */

USE master;
GO

IF SUSER_ID(N'genai_reader') IS NULL
    CREATE LOGIN genai_reader
        WITH PASSWORD = N'REPLACE_WITH_YOUR_OWN_Strong_Pass_123!',
             DEFAULT_DATABASE = GenAI_Demo_DB,
             CHECK_POLICY = ON;
GO

USE GenAI_Demo_DB;
GO

IF USER_ID(N'genai_reader') IS NULL
    CREATE USER genai_reader FOR LOGIN genai_reader;
GO

-- Grant: read every table in this database.
ALTER ROLE db_datareader ADD MEMBER genai_reader;

-- Deny: belt-and-braces protection against any write or code execution.
DENY INSERT, UPDATE, DELETE, ALTER, EXECUTE, REFERENCES
    ON SCHEMA::dbo TO genai_reader;
GO

PRINT 'Read-only user genai_reader is ready.';
GO
