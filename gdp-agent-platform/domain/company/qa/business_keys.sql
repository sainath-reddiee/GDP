-- @pack Business key uniqueness
-- @description One active row per SOURCE_UNIQUE_ID and source system in every COMPANY table
-- @tags reconciliation, business-keys

-- @query duplicate_keys_company_core
-- @severity ERROR
-- @description Active COMPANY_CORE rows sharing a business key
-- @tags duplicates
SELECT SOURCE_UNIQUE_ID, REF_GDP_SOURCE_SYSTEM_SKEY, COUNT(*) AS ROWS_PER_KEY
FROM {{silver}}.COMPANY.COMPANY_CORE
WHERE GDP_IS_ACTIVE
GROUP BY SOURCE_UNIQUE_ID, REF_GDP_SOURCE_SYSTEM_SKEY
HAVING COUNT(*) > 1

-- @query duplicate_keys_company_address
-- @severity WARN
-- @description Active COMPANY_ADDRESS rows sharing a business key
-- @tags duplicates
SELECT SOURCE_UNIQUE_ID, REF_GDP_SOURCE_SYSTEM_SKEY, COUNT(*) AS ROWS_PER_KEY
FROM {{silver}}.COMPANY.COMPANY_ADDRESS
WHERE GDP_IS_ACTIVE
GROUP BY SOURCE_UNIQUE_ID, REF_GDP_SOURCE_SYSTEM_SKEY
HAVING COUNT(*) > 1

-- @query duplicate_keys_company_industry
-- @severity WARN
-- @description Active COMPANY_INDUSTRY rows sharing a business key
-- @tags duplicates
SELECT SOURCE_UNIQUE_ID, REF_GDP_SOURCE_SYSTEM_SKEY, COUNT(*) AS ROWS_PER_KEY
FROM {{silver}}.COMPANY.COMPANY_INDUSTRY
WHERE GDP_IS_ACTIVE
GROUP BY SOURCE_UNIQUE_ID, REF_GDP_SOURCE_SYSTEM_SKEY
HAVING COUNT(*) > 1

-- @query duplicate_keys_company_segment
-- @severity WARN
-- @description Active COMPANY_SEGMENT rows sharing a business key
-- @tags duplicates
SELECT SOURCE_UNIQUE_ID, REF_GDP_SOURCE_SYSTEM_SKEY, COUNT(*) AS ROWS_PER_KEY
FROM {{silver}}.COMPANY.COMPANY_SEGMENT
WHERE GDP_IS_ACTIVE
GROUP BY SOURCE_UNIQUE_ID, REF_GDP_SOURCE_SYSTEM_SKEY
HAVING COUNT(*) > 1

-- @query duplicate_keys_company_relationship
-- @severity WARN
-- @description Active COMPANY_RELATIONSHIP rows sharing a business key
-- @tags duplicates
SELECT SOURCE_UNIQUE_ID, REF_GDP_SOURCE_SYSTEM_SKEY, COUNT(*) AS ROWS_PER_KEY
FROM {{silver}}.COMPANY.COMPANY_RELATIONSHIP
WHERE GDP_IS_ACTIVE
GROUP BY SOURCE_UNIQUE_ID, REF_GDP_SOURCE_SYSTEM_SKEY
HAVING COUNT(*) > 1

-- @query duplicate_keys_company_hierarchy
-- @severity WARN
-- @description Active COMPANY_HIERARCHY rows sharing a business key
-- @tags duplicates
SELECT SOURCE_UNIQUE_ID, REF_GDP_SOURCE_SYSTEM_SKEY, COUNT(*) AS ROWS_PER_KEY
FROM {{silver}}.COMPANY.COMPANY_HIERARCHY
WHERE GDP_IS_ACTIVE
GROUP BY SOURCE_UNIQUE_ID, REF_GDP_SOURCE_SYSTEM_SKEY
HAVING COUNT(*) > 1
