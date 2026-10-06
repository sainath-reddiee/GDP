# Company Domain Contract

Canonical contract for the company silver domain. Unlike opportunity (single hub), the company domain has **6 silver tables** that share `COMPANY_CORE_SKEY` as the FK back to the hub. A single source onboarding (e.g., MTA) typically populates **multiple** of these in one onboarding wave.

Target hub: `DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE` (18 columns).
Spoke tables: `COMPANY_ADDRESS` (55 cols), `COMPANY_INDUSTRY` (14), `COMPANY_SEGMENT` (16), `COMPANY_RELATIONSHIP` (17), `COMPANY_HIERARCHY` (20).

---

## Required Mapping Keys (minimum, hub onboarding)

- `source_unique_id` (often compound, e.g., `Name1 || '||' || Country`)
- `company_name`
- One of: `duns_number`, `legal_entity_name`, `trade_name`, or `alias_name` (for HKEY uniqueness)

---

## 1. COMPANY_CORE — Canonical Column Contract (18)

| # | Column | Type | Nullable | Notes |
|---|--------|------|----------|-------|
| 1 | COMPANY_CORE_SKEY | NUMBER(19,0) | NO | Sequence default |
| 2 | SOURCE_UNIQUE_ID | TEXT | NO | From source PK / compound expression |
| 3 | REF_GDP_SOURCE_SYSTEM_SKEY | NUMBER(38,0) | NO | Cross join to ref_gdp_source_system |
| 4 | DUNS_NUMBER | TEXT | YES | |
| 5 | COMPANY_NAME | TEXT | YES | DBA/trading name |
| 6 | ALIAS_NAME | TEXT | YES | CBRE internal alias |
| 7 | REF_COMPANY_TYPE_SKEY | NUMBER(38,0) | YES | LEFT JOIN ref_company_type |
| 8 | REF_COUNTRY_OF_REGISTRATION_SKEY | NUMBER(38,0) | YES | LEFT JOIN ref_country (via country_mapping) |
| 9 | REF_ADDRESS_SKEY | NUMBER(38,0) | YES | FK to SHARED.ADDRESS |
| 10 | WEBSITE_URL | TEXT | YES | |
| 11 | IS_CBRE_ENTITY | TEXT | YES | Default `'N'` |
| 12 | COMPANY_STATUS | TEXT | YES | Active/Inactive/Dissolved/In Liquidation |
| 13 | COMPANY_CORE_HKEY | TEXT | NO | `m_company_core_hkey()` |
| 14 | GDP_IS_ACTIVE | BOOLEAN | NO | |
| 15 | GDP_INSERTED_TS | TIMESTAMP_NTZ(6) | NO | |
| 16 | GDP_INSERTED_BY | TEXT | NO | |
| 17 | GDP_UPDATED_TS | TIMESTAMP_NTZ(6) | NO | |
| 18 | GDP_UPDATED_BY | TEXT | NO | |

> Note on STTM: STTM lists `LEGAL_ENTITY_NAME` and `TRADE_NAME` but actual silver table does NOT have them (uses `COMPANY_NAME` + `ALIAS_NAME`). Map STTM `Name1` → `COMPANY_NAME`. Reconcile with data team before onboarding.

### COMPANY_CORE — Reference CTE Block

```sql
ref_source_system as (
    {{ m_get_source_system_skey_company('<SOURCE_SYSTEM_NAME>') }}
),

country_mapping as (
    select upper(trim(value_1)) as value_1_norm, target_value
    from {{ source('company_shared_reference', 'gdp_country_mapping') }}
    where model_name = 'country_name_std'
),

ref_country as (
    select country_skey as ref_country_skey, country_name
    from {{ source('company_geo_reference', 'ref_country') }}
    where gdp_is_active = TRUE
),

ref_company_type as (
    select ref_company_type_skey, company_type_desc
    from {{ source('company_reference', 'ref_company_type') }}
),
```

### COMPANY_CORE — Reference JOIN Block

```sql
left join country_mapping as cm
    on upper(trim(o.country_of_registration)) = cm.value_1_norm
left join ref_country as cntry
    on upper(trim(coalesce(cm.target_value, o.country_of_registration))) = upper(trim(cntry.country_name))
left join ref_company_type as ct
    on upper(trim(o.company_type)) = upper(trim(ct.company_type_desc))
```

### COMPANY_CORE — HKEY Block

```python
[
    'duns_number',
    'company_name',
    'alias_name',
    'website_url',
    'is_cbre_entity',
    'company_status'
]
```

### COMPANY_CORE — Final SELECT Block

```sql
source_unique_id,
ref_gdp_source_system_skey,
duns_number,
company_name,
alias_name,
ref_company_type_skey,
ref_country_of_registration_skey,
ref_address_skey,
website_url,
is_cbre_entity,
company_status,
{{ m_company_core_hkey() }} as company_core_hkey,
gdp_is_active,
gdp_inserted_ts,
gdp_inserted_by,
gdp_updated_ts,
gdp_updated_by
```

---

## 2. COMPANY_ADDRESS — Canonical Column Contract (55)

Spoke table. FK back to `COMPANY_CORE.COMPANY_CORE_SKEY`. Targets ESRI-standardized address columns + `SOURCE_*` raw passthroughs.

| # | Column | Type | Nullable |
|---|---|---|---|
| 1 | COMPANY_ADDRESS_SKEY | TEXT | YES (PK) |
| 2 | COMPANY_CORE_SKEY | NUMBER(38,0) | YES (FK to COMPANY_CORE) |
| 3 | REF_ADDRESS_TYPE_SKEY | NUMBER(38,0) | YES |
| 4–20 | ADDRESS_LINE_1, ADDRESS_LINE_2, STREET_NAME, STREET_NUMBER_{1,2,3}, PRE_STREET_DIRECTION_NAME, STREET_TYPE, POST_STREET_DIRECTION_NAME, CITY, STATE, COUNTRY, COUNTY, POSTAL_CODE, POSTAL_CODE_EXTENSION | TEXT | YES (ESRI standardized) |
| 19–20 | LATITUDE, LONGITUDE | NUMBER(12,8) | YES |
| 21–41 | SOURCE_ADDRESS_LINE_1, SOURCE_ADDRESS_LINE_2, SOURCE_STREET_NAME, SOURCE_STREET_NUMBER_{1,2,3}, SOURCE_PRE_STREET_DIRECTION_NAME, SOURCE_STREET_TYPE, SOURCE_POST_STREET_DIRECTION_NAME, SOURCE_CITY, SOURCE_STATE, SOURCE_COUNTRY_NAME, SOURCE_COUNTY, SOURCE_POSTAL_CODE, SOURCE_POSTAL_CODE_EXTENSION, SOURCE_LATITUDE, SOURCE_LONGITUDE, SOURCE_GEOCODE_OUTPUT, SOURCE_LATITUDE_OVERRIDE, SOURCE_LONGITUDE_OVERRIDE, SOURCE_GEOCODE_OVERRIDE_SOURCE | TEXT | YES (raw passthrough) |
| 42–46 | ADDRESS_MATCH_SCORE, ADDRESS_MATCH_CODE, ADDRESS_PRECISION_CODE, GEOCODE_OUTPUT, GEOCODE_PROVIDER_NAME | TEXT | YES (ESRI output) |
| 47 | COMPANY_ADDRESS_HKEY | TEXT | YES |
| 48 | SOURCE_UNIQUE_ID | TEXT | YES |
| 49 | REF_GDP_SOURCE_SYSTEM_SKEY | NUMBER(38,0) | YES |
| 50–54 | GDP_IS_ACTIVE, GDP_INSERTED_TS, GDP_INSERTED_BY, GDP_UPDATED_TS, GDP_UPDATED_BY | (audit) | YES |
| 55 | COMPANY_ADDRESS_SKEY_NEW | NUMBER(19,0) | YES (transition column) |

> **Pattern**: Source onboarding populates `SOURCE_*` raw fields + `SOURCE_UNIQUE_ID` + `COMPANY_CORE_SKEY` + audit. ESRI-standardized fields (`ADDRESS_LINE_1` etc.) populated by a downstream ESRI step, not the source-onboarding model.

### COMPANY_ADDRESS — HKEY Block

```python
[
    'source_address_line_1', 'source_address_line_2',
    'source_city', 'source_state', 'source_country_name',
    'source_postal_code', 'source_latitude', 'source_longitude'
]
```

---

## 3. COMPANY_INDUSTRY — Canonical Column Contract (14)

| # | Column | Type | Nullable |
|---|---|---|---|
| 1 | COMPANY_INDUSTRY_SKEY | NUMBER(38,0) | NO (sequence) |
| 2 | SOURCE_UNIQUE_ID | TEXT | NO |
| 3 | REF_GDP_SOURCE_SYSTEM_SKEY | NUMBER(38,0) | NO |
| 4 | COMPANY_CORE_SKEY | NUMBER(38,0) | YES (FK) |
| 5 | REF_INDUSTRY_NAME_SKEY | NUMBER(38,0) | YES |
| 6 | SOURCE_NAICS_CODE | TEXT | YES |
| 7 | SOURCE_NAICS_DESCRIPTION | TEXT | YES |
| 8 | IS_PRIMARY_INDUSTRY | BOOLEAN | YES |
| 9 | COMPANY_INDUSTRY_HKEY | TEXT | NO |
| 10–14 | GDP_IS_ACTIVE, GDP_INSERTED_TS, GDP_INSERTED_BY, GDP_UPDATED_TS, GDP_UPDATED_BY | (audit) | NO |

> STTM column `INDUSTRY_NAME` does NOT exist on silver table — silver uses `REF_INDUSTRY_NAME_SKEY` (FK). Map source industry-name text via `LEFT JOIN ref_industry_name`.

### COMPANY_INDUSTRY — HKEY Block

```python
[
    'ref_industry_name_skey', 'source_naics_code',
    'source_naics_description', 'is_primary_industry'
]
```

---

## 4. COMPANY_SEGMENT — Canonical Column Contract (16)

| # | Column | Type | Nullable |
|---|---|---|---|
| 1 | COMPANY_SEGMENT_SKEY | NUMBER(38,0) | NO (sequence) |
| 2 | SOURCE_UNIQUE_ID | TEXT | NO |
| 3 | REF_GDP_SOURCE_SYSTEM_SKEY | NUMBER(38,0) | NO |
| 4 | COMPANY_CORE_SKEY | NUMBER(38,0) | YES (FK) |
| 5 | REF_SEGMENT_SKEY | NUMBER(38,0) | YES |
| 6 | REF_SERVICE_LINE_SKEY | NUMBER(38,0) | YES |
| 7 | VALID_FROM | TIMESTAMP_NTZ | YES |
| 8 | VALID_TO | TIMESTAMP_NTZ | YES |
| 9 | COMPANY_SEGMENT_HKEY | TEXT | NO |
| 10 | SOURCE_SEGMENT_NAME | TEXT | YES |
| 11 | SOURCE_SERVICE_LINE_NAME | TEXT | YES |
| 12–16 | GDP_IS_ACTIVE, GDP_INSERTED_TS, GDP_INSERTED_BY, GDP_UPDATED_TS, GDP_UPDATED_BY | (audit) | NO |

### COMPANY_SEGMENT — HKEY Block

```python
[
    'ref_segment_skey', 'ref_service_line_skey',
    'source_segment_name', 'source_service_line_name',
    'valid_from', 'valid_to'
]
```

---

## 5. COMPANY_RELATIONSHIP — Canonical Column Contract (17)

| # | Column | Type | Nullable |
|---|---|---|---|
| 1 | COMPANY_RELATIONSHIP_SKEY | NUMBER(38,0) | NO (sequence) |
| 2 | SOURCE_UNIQUE_ID | TEXT | NO |
| 3 | REF_GDP_SOURCE_SYSTEM_SKEY | NUMBER(38,0) | NO |
| 4 | COMPANY_CORE_SKEY | NUMBER(38,0) | YES (FK) |
| 5 | REF_RELATIONSHIP_TYPE_SKEY | NUMBER(38,0) | YES |
| 6 | ACCOUNT_NAME | TEXT | YES |
| 7 | ACCOUNT_NUMBER | NUMBER(38,0) | YES |
| 8 | ACCOUNT_STATUS | TEXT | YES |
| 9 | SOURCE_ACCOUNT_ID | TEXT | YES |
| 10 | RELATIONSHIP_START_DATE | TIMESTAMP_NTZ | YES |
| 11 | RELATIONSHIP_END_DATE | TIMESTAMP_NTZ | YES |
| 12 | COMPANY_RELATIONSHIP_HKEY | TEXT | NO |
| 13–17 | GDP_IS_ACTIVE, GDP_INSERTED_TS, GDP_INSERTED_BY, GDP_UPDATED_TS, GDP_UPDATED_BY | (audit) | NO |

### COMPANY_RELATIONSHIP — HKEY Block

```python
[
    'ref_relationship_type_skey', 'account_name', 'account_number',
    'account_status', 'source_account_id',
    'relationship_start_date', 'relationship_end_date'
]
```

---

## 6. COMPANY_HIERARCHY — Canonical Column Contract (20)

| # | Column | Type | Nullable |
|---|---|---|---|
| 1 | COMPANY_HIERARCHY_SKEY | NUMBER(38,0) | NO (sequence) |
| 2 | SOURCE_UNIQUE_ID | TEXT | NO |
| 3 | REF_GDP_SOURCE_SYSTEM_SKEY | NUMBER(38,0) | NO |
| 4 | COMPANY_CORE_SKEY | NUMBER(38,0) | YES (FK) |
| 5 | COMPANY_NAME | TEXT | YES |
| 6 | REF_COMPANY_TYPE_SKEY | NUMBER(38,0) | YES |
| 7 | DOMESTIC_PARENT_SKEY | NUMBER(38,0) | YES (NOT `REF_DOMESTIC_PARENT_SKEY` per STTM) |
| 8 | GLOBAL_PARENT_SKEY | NUMBER(38,0) | YES |
| 9 | HIERARCHY_LEVEL | NUMBER(10,0) | YES |
| 10 | HIERARCHY_PATH | TEXT | YES (pipe-delimited SKEY chain) |
| 11 | VALID_FROM | TIMESTAMP_NTZ | YES |
| 12 | VALID_TO | TIMESTAMP_NTZ | YES |
| 13 | REF_HIERARCHY_TYPE_SKEY | NUMBER(38,0) | YES |
| 14 | COMPANY_HIERARCHY_HKEY | TEXT | NO |
| 15–19 | GDP_IS_ACTIVE, GDP_INSERTED_TS, GDP_INSERTED_BY, GDP_UPDATED_TS, GDP_UPDATED_BY | (audit) | NO |
| 20 | IMMEDIATE_PARENT_SKEY | NUMBER(38,0) | YES |

### COMPANY_HIERARCHY — HKEY Block

```python
[
    'company_name', 'ref_company_type_skey',
    'domestic_parent_skey', 'global_parent_skey', 'immediate_parent_skey',
    'hierarchy_level', 'hierarchy_path',
    'ref_hierarchy_type_skey', 'valid_from', 'valid_to'
]
```

---

## Type Cast Reference (base CTE only)

| Target Column | Target Type | Common Bronze Type | Cast |
|---|---|---|---|
| LATITUDE / LONGITUDE | NUMBER(12,8) | TEXT or FLOAT | `o.col::number(12,8)` |
| ACCOUNT_NUMBER | NUMBER(38,0) | TEXT | `try_to_number(o.col)` |
| RELATIONSHIP_START_DATE / END_DATE | TIMESTAMP_NTZ | DATE | `o.col::timestamp_ntz` |
| VALID_FROM / VALID_TO | TIMESTAMP_NTZ | DATE | `o.col::timestamp_ntz` |
| HIERARCHY_LEVEL | NUMBER(10,0) | TEXT | `try_to_number(o.col)` |

---

## Multi-Source-Table Extraction Pattern (MTA-style)

When a single source spans multiple bronze tables (e.g., MTA = `PS_CUSTOMER` + `PS_VENDOR`), use a UNION ALL extraction CTE:

```sql
with sp_companies as (
    -- Customers
    select distinct
        upper(trim(c."Name1")) || '||' || coalesce(upper(trim(ca."COUNTRY")), '') as source_unique_id,
        NULLIF(TRIM(c."Name1"), '') as company_name,
        -- ... other customer-side fields
        c.GDP_IS_ACTIVE, c.GDP_INSERTED_TS, c.GDP_UPDATED_TS
    from {{ source('company_mta_source', 'PS_CUSTOMER') }} c
    left join {{ source('company_mta_source', 'PS_CUST_ADDRESS') }} ca
        on c."CUST_ID" = ca."CUST_ID" and ca."EFF_STATUS" = 'A'
    where c."CUST_STATUS" = 'A' and c."Name1" is not null

    union all

    -- Vendors
    select distinct
        upper(trim(v."Name1")) || '||' || coalesce(upper(trim(va."COUNTRY")), '') as source_unique_id,
        NULLIF(TRIM(v."Name1"), '') as company_name,
        -- ... other vendor-side fields (same alias names!)
        v.GDP_IS_ACTIVE, v.GDP_INSERTED_TS, v.GDP_UPDATED_TS
    from {{ source('company_mta_source', 'PS_VENDOR') }} v
    left join {{ source('company_mta_source', 'PS_VENDOR_ADDR') }} va
        on v."VENDOR_ID" = va."VENDOR_ID" and va."EFF_STATUS" = 'A'
    where v."VENDOR_STATUS" = 'A' and v."Name1" is not null
)
qualify row_number() over (
    partition by source_unique_id
    order by greatest(coalesce(gdp_updated_ts, '1900-01-01'::timestamp_ntz),
                       coalesce(gdp_inserted_ts, '1900-01-01'::timestamp_ntz)) desc
) = 1
```

Key rules for the UNION pattern:
- Each branch outputs **identical aliases** (column names) and **same order**
- Compound `SOURCE_UNIQUE_ID` MUST be deterministic and survive nulls (`'||'` delimiter, `coalesce(...,'')` sentinel)
- `qualify` runs **after** the union to dedup across branches

---

## Multi-Target Onboarding Pattern (MTA-style)

A single source onboarding wave generates **N ephemeral staging models** + **N hub patches**, one per target spoke:

| File | Target |
|---|---|
| `models/silver/company/mta/mta_company_core.sql` | COMPANY_CORE |
| `models/silver/company/mta/mta_company_address.sql` | COMPANY_ADDRESS |
| `models/silver/company/mta/mta_company_industry.sql` | COMPANY_INDUSTRY |
| `models/silver/company/mta/mta_company_segment.sql` | COMPANY_SEGMENT |
| `models/silver/company/mta/mta_company_relationship.sql` | COMPANY_RELATIONSHIP |
| `models/silver/company/mta/mta_company_hierarchy.sql` | COMPANY_HIERARCHY |

Each ephemeral spoke model joins back to `{{ ref('company_core') }}` to resolve `COMPANY_CORE_SKEY` from `SOURCE_UNIQUE_ID`. Watermark rows: one per target.

---

## Bronze MTA Source Tables (verified)

| Table | Rows | Notes |
|---|---|---|
| PS_CUSTOMER | 744K | Customer master |
| PS_CUST_ADDRESS | 922K | Customer address (multi-row per customer; filter `EFF_STATUS = 'A'`) |
| PS_VENDOR | 364K | Vendor master |
| PS_VENDOR_ADDR | 610K | Vendor address (filter `EFF_STATUS = 'A'`) |
| PS_CUST_ADDR_SEQ | 922K | Address sequence (optional) |
| PS_CBTA_DEAL_HDR | 834K | (Property/opportunity domain — not company) |
| PS_CBTA_DEAL_LINE | 3.5M | (Property/opportunity domain) |
| PS_CBTA_PROPERTY | 398K | (Property domain) |

## Known Sources to Onboard

| SKEY | Source | Status |
|---|---|---|
| 107 | SPOC | Planned |
| 110 | INTROHIVE | Planned |
| 112 | BUSINESS_SMARTSHEET | Planned |
| 115 | CLIENT_SENTIMENT (NPS) | Planned |
| 120 | NEWS_TO_LEADS | Planned |
| 123 | TAT | Planned |
| 201 | MTA | **In progress** (this contract) |
