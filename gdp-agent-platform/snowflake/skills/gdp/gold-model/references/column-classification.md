# Gold — Column Classification Rules

Gold DDL generation does **NOT** reorder columns. The column order from the data dictionary or Excel spec is preserved exactly as provided.

This document describes how to **identify** each column's role (for type/nullability assignment) without changing its position. `{PREFIX}` is the project-configurable audit-column namespace gathered in Step 1 of SKILL.md (may be blank).

---

## Column Role Identification

Identify each column's role using the rules below. Role determines data type and nullability — it does NOT change position.

### Role: Own Surrogate Key (PK)

- Column name ends with `_SKEY` AND the Key column in the spec says `PK`
- Examples: `DIM_CLIENT_SKEY`, `FACT_OPP_PROJECT_SKEY`, `OPPORTUNITY_STAGE_DIM_SKEY`
- Always `LONG NOT NULL`

### Role: Foreign Key (FK SKEY)

- Column name ends with `_SKEY` AND the Key column in the spec says `FK`
- Examples: `OPPORTUNITY_FACT_SKEY`, `DATE_PROJECT_START_DIM_SKEY`, `OPPORTUNITY_STAGE_DIM_SKEY`
- Type: `LONG`; nullable per spec

### Role: IS_* Flag

- Column name starts with `IS_`
- Examples: `IS_ACTIVE`, `IS_TERMINAL_STAGE`
- Type: `BOOLEAN DEFAULT FALSE`; nullable per spec but always carry `DEFAULT FALSE`

### Role: Business Date / Timestamp

- Column name ends with `_DATE`, `_TS`, `EXPIRY_DATE`, `VALID_FROM`, `VALID_TO`, etc.
- Examples: `PROJECT_START_DATE`, `EXPIRY_DATE`, `RENEWAL_EXPIRATION_DATE`
- Source type `DATE` → use `DATE`; source type `TIMESTAMP_NTZ(6)` / `TIMESTAMP_NTZ` → use `TIMESTAMP_NTZ`

### Role: Numeric Measure / Amount

- Column name ends with `_AMOUNT`, `_QTY`, `_QUANTITY`, `_COUNT`, `_RATE`, `_PCT`, `_PERCENTAGE`, `_LATITUDE`, `_LONGITUDE`
- Financial / monetary: `NUMBER(38,5)`; integer counts: `NUMBER(38,0)`; percentages/ratios: `NUMBER(10,5)`; geo measures: `NUMBER(15,8)`; default: `NUMBER(38,5)`
- Examples: `TOTAL_CONTRACT_VALUE`, `PROJECT_DURATION_DAYS`

### Role: Audit Column

- Column name starts with `{PREFIX}_`
- Fixed types — see data-types.md Zone 4 table
- Must always be last in column list

### Role: HKEY (MANDATORY)

- Column name ends with `_HKEY`
- **Gold tables ALWAYS include `{TABLE_NAME}_HKEY STRING NOT NULL`** positioned immediately above the audit columns. Rename source `MD5_HASH` (or similar single hash) to `{TABLE_NAME}_HKEY`; inject if absent.

### Role: General Business Attribute

- Everything else: text, codes, names, descriptions, IDs, etc.
- Default type: `STRING NULL`

---

## Column Order Policy

| Rule | Detail |
|---|---|
| **Do not reorder** | Keep every column in the exact position it appears in the spec |
| **Audit columns** | Must be the last 5 columns. If they are already last in the spec, keep them. If they appear elsewhere, note this as a REVIEW item |
| **HKEY column** | Always present as `{TABLE_NAME}_HKEY STRING NOT NULL`, positioned directly above the audit block. Rename source `MD5_HASH` to `{TABLE_NAME}_HKEY` if present; otherwise inject. |
| **SOURCE_UNIQUE_ID** | Not a Gold column — omit if present |
| **REF_{PREFIX}_SOURCE_SYSTEM_SKEY** | Not a Gold column — omit if present |

---

## Naming Fixes (applied silently with REVIEW comment)

| Issue | Fix |
|---|---|
| Column name contains a space | Replace space with `_` — e.g. `CLIENT CLASSIFICATION` → `CLIENT_CLASSIFICATION` |
| Leading/trailing whitespace | Trim silently |
| All other names | Preserve exactly as in spec |
| Column name ends with `_F` (excluding `ACTIVE_F` and any `{PREFIX}_*` column) | Rename suffix `_F` to `_FLAG` (e.g. `SENT_F` → `SENT_FLAG`) |
