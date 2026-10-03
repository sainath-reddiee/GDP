# Property Domain Contract — STUB

> **Status**: Stub. Populate before first property-domain onboarding.

Target hub model: `DEV_GDP_SILVER_DB.PROPERTY.PROPERTY_CORE` (TBD column count).

To complete this contract, copy the structure from `opportunity-contract.md` and replace each section with property-domain specifics.

---

## Required Sections (to be filled)

### 1. Required Mapping Keys (minimum)
List the must-have aliases from `column_map`, e.g.:
- `source_unique_id`
- `property_name`
- `property_address`
- `property_usage_desc`

### 2. Canonical Column Contract
DDL-order table of every column in `PROPERTY_CORE` with `#`, `Column`, `Type`, `Nullable`, `Notes`.

Run this to bootstrap:
```sql
DESCRIBE TABLE DEV_GDP_SILVER_DB.PROPERTY.PROPERTY_CORE;
```

### 3. Reference CTE Block
Paste-ready CTEs for each FK reference table, e.g.:
- `ref_property_usage`
- `ref_property_type`
- `ref_country`
- `ref_global_region`
- `ref_market`
- `ref_sub_market`
- `country_mapping`

### 4. Reference JOIN Block
Paste-ready `LEFT JOIN ... on upper(trim(...)) = upper(trim(...))` clauses for the base CTE.

### 5. HKEY Block
Python list of business/descriptive columns (DDL order) for `m_property_core_hkey`.
EXCLUDE: `*_SKEY`, `SOURCE_UNIQUE_ID`, `PROPERTY_CORE_HKEY`, all `GDP_*` audit cols.

### 6. Type Cast Reference
Table of target columns where bronze→silver type cast is required in the base CTE.
Property domain commonly has DATE→TIMESTAMP_NTZ casts (sale dates, build dates) and NUMBER→FLOAT casts (sqft, lot size).

### 7. Final SELECT Block
Paste-ready column list (DDL order) for the ephemeral model's terminal SELECT.

### 8. Reference Implementation
Path to an existing source implementation once one is built (will become the canonical example for new sources).

---

## Known Sources to Onboard

Per `AGENTS.md` source registry:

| SKEY | Source | Status |
|---|---|---|
| 100 | EDP | Planned |
| 102 | LIGHTBOX | Planned |
| 200 | DIQ | Planned |
| 201 | MTA | Planned (shared with company) |
