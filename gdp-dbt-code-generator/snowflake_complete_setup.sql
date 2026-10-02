-- ========================================
-- Snowflake Complete Setup Script
-- DBT Code Generator Application
-- ========================================
-- Run this script as ACCOUNTADMIN or a role with appropriate privileges

-- ========================================
-- Part 1: Create Warehouse
-- ========================================

USE ROLE ACCOUNTADMIN;

CREATE WAREHOUSE IF NOT EXISTS DBT_GENERATOR_WH
    WITH 
    WAREHOUSE_SIZE = 'XSMALL'
    AUTO_SUSPEND = 300
    AUTO_RESUME = TRUE
    COMMENT = 'Warehouse for DBT Code Generator application';

-- ========================================
-- Part 2: Create Database and Schema
-- ========================================

CREATE DATABASE IF NOT EXISTS DBT_GENERATOR
    COMMENT = 'Database for DBT Code Generator application';

CREATE SCHEMA IF NOT EXISTS DBT_GENERATOR.APP
    COMMENT = 'Schema containing application tables';

USE SCHEMA DBT_GENERATOR.APP;

-- ========================================
-- Part 3: Create Application Tables
-- ========================================

-- Drop existing tables if they exist (for clean setup)
DROP TABLE IF EXISTS DBT_FILES;
DROP TABLE IF EXISTS MAPPING_ROWS;
DROP TABLE IF EXISTS PROJECTS;
DROP TABLE IF EXISTS MACRO_LIBRARY;

-- Drop existing sequences if they exist
DROP SEQUENCE IF EXISTS DBT_FILES_SEQ;
DROP SEQUENCE IF EXISTS MAPPING_ROWS_SEQ;
DROP SEQUENCE IF EXISTS PROJECTS_SEQ;
DROP SEQUENCE IF EXISTS MACROS_SEQ;

-- Create PROJECTS table
CREATE OR REPLACE SEQUENCE PROJECTS_SEQ START = 1 INCREMENT = 1;

CREATE TABLE PROJECTS (
    ID NUMBER DEFAULT PROJECTS_SEQ.NEXTVAL,
    PROJECT_NAME VARCHAR(255) NOT NULL,
    DESCRIPTION TEXT,
    DBT_STATUS VARCHAR(50) DEFAULT 'NOT_GENERATED',
    CONSTRAINT PK_PROJECTS PRIMARY KEY (ID)
);

-- Create MAPPING_ROWS table
CREATE OR REPLACE SEQUENCE MAPPING_ROWS_SEQ START = 1 INCREMENT = 1;

CREATE TABLE MAPPING_ROWS (
    ID NUMBER DEFAULT MAPPING_ROWS_SEQ.NEXTVAL,
    PROJECT_ID NUMBER NOT NULL,
    SOURCE_SCHEMA VARCHAR(255),
    TARGET_SCHEMA VARCHAR(255),
    SOURCE_TABLE_NAME VARCHAR(255),
    TARGET_TABLE_NAME VARCHAR(255),
    SOURCE_COLUMN VARCHAR(255),
    TARGET_COLUMN VARCHAR(255),
    SOURCE_DESCRIPTION TEXT,
    TARGET_DESCRIPTION TEXT,
    SOURCE_DATA_TYPE VARCHAR(100),
    TARGET_DATA_TYPE VARCHAR(100),
    MAPPING_SIMILARITY FLOAT,
    TRANSFORMATION_LOGIC TEXT DEFAULT '',
    CLEANING_LOGIC TEXT DEFAULT '',
    MERGE_STRATEGY VARCHAR(100) DEFAULT 'UNION',
    MACROS TEXT DEFAULT '',
    CONSTRAINT PK_MAPPING_ROWS PRIMARY KEY (ID),
    CONSTRAINT FK_PROJECT_ID FOREIGN KEY (PROJECT_ID) REFERENCES PROJECTS(ID) ON DELETE CASCADE
);

-- Create DBT_FILES table
CREATE OR REPLACE SEQUENCE DBT_FILES_SEQ START = 1 INCREMENT = 1;

CREATE TABLE DBT_FILES (
    ID NUMBER DEFAULT DBT_FILES_SEQ.NEXTVAL,
    PROJECT_ID NUMBER NOT NULL,
    FILE_PATH VARCHAR(500),
    FILE_CONTENT TEXT,
    CONSTRAINT PK_DBT_FILES PRIMARY KEY (ID),
    CONSTRAINT FK_DBT_PROJECT_ID FOREIGN KEY (PROJECT_ID) REFERENCES PROJECTS(ID) ON DELETE CASCADE
);

-- Create MACRO_LIBRARY table
CREATE OR REPLACE SEQUENCE MACROS_SEQ START = 1 INCREMENT = 1;

CREATE TABLE MACRO_LIBRARY (
    ID NUMBER DEFAULT MACROS_SEQ.NEXTVAL,
    NAME VARCHAR(255) NOT NULL UNIQUE,
    DESCRIPTION TEXT,
    SQL_CONTENT TEXT,
    CONSTRAINT PK_MACRO_LIBRARY PRIMARY KEY (ID)
);

-- ========================================
-- Part 4: Create Service Role
-- ========================================

CREATE ROLE IF NOT EXISTS DBT_SERVICE_ROLE
    COMMENT = 'Service role for DBT Code Generator application';

-- ========================================
-- Part 5: Grant Privileges to Service Role
-- ========================================

-- Warehouse privileges
GRANT USAGE ON WAREHOUSE DBT_GENERATOR_WH TO ROLE DBT_SERVICE_ROLE;

-- Database privileges
GRANT USAGE ON DATABASE DBT_GENERATOR TO ROLE DBT_SERVICE_ROLE;

-- Schema privileges
GRANT USAGE ON SCHEMA DBT_GENERATOR.APP TO ROLE DBT_SERVICE_ROLE;

-- Table privileges
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA DBT_GENERATOR.APP TO ROLE DBT_SERVICE_ROLE;
GRANT SELECT, INSERT, UPDATE, DELETE ON FUTURE TABLES IN SCHEMA DBT_GENERATOR.APP TO ROLE DBT_SERVICE_ROLE;

-- Sequence privileges (for autoincrement)
GRANT USAGE ON ALL SEQUENCES IN SCHEMA DBT_GENERATOR.APP TO ROLE DBT_SERVICE_ROLE;
GRANT USAGE ON FUTURE SEQUENCES IN SCHEMA DBT_GENERATOR.APP TO ROLE DBT_SERVICE_ROLE;

-- ========================================
-- Part 6A: Create Service User (Key-Pair Auth)
-- ========================================

-- OPTION A: Service account with key-pair authentication (RECOMMENDED)
-- 
-- Before running this, generate your key pair:
--   openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out snowflake_key.p8 -nocrypt
--   openssl rsa -in snowflake_key.p8 -pubout -out snowflake_key.pub
--
-- Then get the public key string (remove headers/footers):
--   cat snowflake_key.pub | grep -v "BEGIN PUBLIC" | grep -v "END PUBLIC" | tr -d '\n'

CREATE USER IF NOT EXISTS DBT_SERVICE_USER
    DEFAULT_ROLE = DBT_SERVICE_ROLE
    DEFAULT_WAREHOUSE = DBT_GENERATOR_WH
    COMMENT = 'Service account for DBT Code Generator';

-- Set the RSA public key (replace with your actual public key string)
-- ALTER USER DBT_SERVICE_USER SET RSA_PUBLIC_KEY='MIIBIjANBgkqhki...YOUR_PUBLIC_KEY_HERE...AQAB';

-- Grant role to user
GRANT ROLE DBT_SERVICE_ROLE TO USER DBT_SERVICE_USER;

-- ========================================
-- Part 6B: Create User (Password Auth) [ALTERNATIVE]
-- ========================================

-- OPTION B: Regular user with password authentication
-- Uncomment the following lines if you prefer password auth instead of key-pair

-- CREATE USER IF NOT EXISTS DBT_APP_USER
--     PASSWORD = '<YOUR_STRONG_PASSWORD>'
--     DEFAULT_ROLE = DBT_SERVICE_ROLE
--     DEFAULT_WAREHOUSE = DBT_GENERATOR_WH
--     MUST_CHANGE_PASSWORD = FALSE
--     COMMENT = 'Application user for DBT Code Generator';
-- 
-- GRANT ROLE DBT_SERVICE_ROLE TO USER DBT_APP_USER;

-- ========================================
-- Part 7: Grant Cortex AI Access (For AI Features)
-- ========================================

-- Grant access to Snowflake Cortex for AI-powered transformations
GRANT USAGE ON DATABASE SNOWFLAKE TO ROLE DBT_SERVICE_ROLE;
GRANT USAGE ON SCHEMA SNOWFLAKE.CORTEX TO ROLE DBT_SERVICE_ROLE;

-- Grant the Cortex application role to enable function execution
-- This is required to actually execute CORTEX functions
GRANT APPLICATION ROLE SNOWFLAKE.CORTEX_USER TO ROLE DBT_SERVICE_ROLE;

-- ========================================
-- Part 8: Verification
-- ========================================

-- Verify warehouse
SHOW WAREHOUSES LIKE 'DBT_GENERATOR_WH';

-- Verify database and schema
SHOW DATABASES LIKE 'DBT_GENERATOR';
SHOW SCHEMAS IN DATABASE DBT_GENERATOR;

-- Verify tables
USE SCHEMA DBT_GENERATOR.APP;
SHOW TABLES;

-- Verify users
SHOW USERS LIKE 'DBT_SERVICE_USER';
-- SHOW USERS LIKE 'DBT_APP_USER';  -- Uncomment if using password auth

-- Verify role
SHOW ROLES LIKE 'DBT_SERVICE_ROLE';

-- Verify grants
SHOW GRANTS TO ROLE DBT_SERVICE_ROLE;

-- ========================================
-- Setup Complete!
-- ========================================
-- 
-- NEXT STEPS:
-- 1. If using key-pair auth:
--    - Uncomment and run the ALTER USER command above with your public key
--    - Copy the private key file (snowflake_key.p8) to your deployment directory
-- 
-- 2. Update your .env file with these values:
--    SNOWFLAKE_ACCOUNT=<your_account>
--    SNOWFLAKE_USER=DBT_SERVICE_USER  (or DBT_APP_USER if using password)
--    SNOWFLAKE_WAREHOUSE=DBT_GENERATOR_WH
--    SNOWFLAKE_DATABASE=DBT_GENERATOR
--    SNOWFLAKE_SCHEMA=APP
--    SNOWFLAKE_ROLE=DBT_SERVICE_ROLE
-- 
-- 3. Follow the deployment guide to deploy your application
-- ========================================
