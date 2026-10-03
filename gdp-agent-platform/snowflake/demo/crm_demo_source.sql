-- Demo source for the onboarding walkthrough: a small CRM system in its own database.
-- Idempotent: objects are created if missing and rows are inserted only into empty tables.

CREATE DATABASE IF NOT EXISTS {{demo_database}} COMMENT = 'Demo CRM source system for the onboarding platform';
CREATE SCHEMA IF NOT EXISTS {{demo_database}}.CRM COMMENT = 'Operational CRM extract';

CREATE TABLE IF NOT EXISTS {{demo_database}}.CRM.CRM_CUSTOMER (
    CUST_ID         VARCHAR(10)  NOT NULL COMMENT 'CRM customer identifier',
    FIRST_NM        VARCHAR(50)           COMMENT 'Given name',
    LAST_NM         VARCHAR(50)           COMMENT 'Family name',
    EMAIL_ADDR      VARCHAR(120)          COMMENT 'Primary email',
    PHONE_NO        VARCHAR(20)           COMMENT 'Primary phone',
    BIRTH_DT        DATE                  COMMENT 'Date of birth',
    CUST_STATUS_CD  VARCHAR(2)            COMMENT 'A=active, I=inactive, P=prospect'
);

CREATE TABLE IF NOT EXISTS {{demo_database}}.CRM.CRM_ORDER (
    ORDER_ID     VARCHAR(12)   NOT NULL COMMENT 'Order number',
    CUST_ID      VARCHAR(10)   NOT NULL COMMENT 'Ordering customer',
    ORDER_TS     TIMESTAMP_NTZ          COMMENT 'Order timestamp (UTC)',
    ORDER_AMT    NUMBER(12,2)           COMMENT 'Order total',
    CURRENCY_CD  VARCHAR(3)             COMMENT 'ISO 4217 currency'
);

INSERT INTO {{demo_database}}.CRM.CRM_CUSTOMER
SELECT 'C' || LPAD(n::VARCHAR, 6, '0'),
       GET(ARRAY_CONSTRUCT('Asha','Ravi','Maria','John','Wei','Fatima','Lucas','Priya','Omar','Elena'), MOD(n, 10))::VARCHAR,
       GET(ARRAY_CONSTRUCT('Rao','Smith','Garcia','Chen','Khan','Silva','Iyer','Novak','Brown','Haddad','Kim'), MOD(n, 11))::VARCHAR,
       IFF(MOD(n, 17) = 0, NULL, 'user' || n || '@example.com'),
       IFF(MOD(n, 9) = 0, NULL, '+1-555-' || LPAD(MOD(n * 7919, 10000)::VARCHAR, 4, '0')),
       DATEADD(day, -MOD(n * 2654435761, 20000) - 6570, '2026-01-01'::DATE),
       GET(ARRAY_CONSTRUCT('A','A','A','I','P'), MOD(n, 5))::VARCHAR
  FROM (SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) AS n FROM TABLE(GENERATOR(ROWCOUNT => 500)))
 WHERE NOT EXISTS (SELECT 1 FROM {{demo_database}}.CRM.CRM_CUSTOMER);

INSERT INTO {{demo_database}}.CRM.CRM_ORDER
SELECT 'O' || LPAD(n::VARCHAR, 8, '0'),
       'C' || LPAD((MOD(n * 37, 500) + 1)::VARCHAR, 6, '0'),
       DATEADD(minute, n * 97, '2025-01-01 00:00:00'::TIMESTAMP_NTZ),
       ROUND(10 + MOD(n * 7919, 50000) / 100, 2),
       GET(ARRAY_CONSTRUCT('USD','USD','USD','EUR','GBP'), MOD(n, 5))::VARCHAR
  FROM (SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) AS n FROM TABLE(GENERATOR(ROWCOUNT => 2000)))
 WHERE NOT EXISTS (SELECT 1 FROM {{demo_database}}.CRM.CRM_ORDER);
