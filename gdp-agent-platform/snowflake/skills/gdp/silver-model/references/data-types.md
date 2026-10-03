# Silver — Data Types & Nullability Rules

Apply these rules AFTER classifying columns into zones using `./column-classification.md`. `{PREFIX}` is the project-configurable audit-column namespace gathered in Step 1 of SKILL.md (may be blank).

---

## Zone 1 — Key Columns

| Column | Snowflake DDL Type | Nullable | Default / Notes |
|---|---|---|---|
| `{ENTITY}_SKEY` (own PK) | `LONG` | `NOT NULL` | No DEFAULT in DDL — NEXTVAL assigned in dbt SELECT |
| `SOURCE_UNIQUE_ID` | `STRING` | `NOT NULL` | — |
| `REF_{PREFIX}_SOURCE_SYSTEM_SKEY` | `LONG` | `NOT NULL` | — |
| `{PARENT_ENTITY}_SKEY` (parent FK) | `LONG` | `NOT NULL` | — |

**Zone 1 rule:** All Zone 1 columns are `NOT NULL`.

---

## Zone 2 — Business Columns

### Reference FK columns (`REF_*_SKEY`)
| Pattern | Type | Nullable |
|---|---|---|
| `REF_{LOOKUP_TABLE}_SKEY` | `LONG` | `NULL` (unless business rule mandates otherwise) |

### Domain attribute columns
| Pattern / Name | Type | Nullable | Notes |
|---|---|---|---|
| `*_NAME`, `*_TITLE` | `STRING` | `NULL` | |
| `*_CODE` | `STRING` | `NULL` | |
| `*_NUMBER` | `STRING` | `NULL` | Even numeric IDs stored as STRING (e.g., DUNS_NUMBER) |
| `*_URL`, `*_URI` | `STRING` | `NULL` | |
| `*_DESCRIPTION`, `*_DESC` | `STRING` | `NULL` | |
| `*_STATUS` | `STRING` | `NULL` | |
| `*_TYPE` | `STRING` | `NULL` | |
| `*_LEVEL` | `STRING` | `NULL` | |
| `*_PATH` | `STRING` | `NULL` | |
| `*_EMAIL` | `STRING` | `NULL` | |
| `*_PHONE` | `STRING` | `NULL` | |
| `LATITUDE`, `LONGITUDE` | `FLOAT` | `NULL` | |
| `*_SCORE`, `*_MATCH_SCORE` | `FLOAT` | `NULL` | |
| `*_COUNT`, `*_AMOUNT`, `*_QUANTITY` | `DECIMAL(38, 6)` | `NULL` | Adjust precision as needed |
| `*_RATE`, `*_PERCENTAGE`, `*_PCT` | `DECIMAL(18, 6)` | `NULL` | |
| `*_ID` (non-PK, non-SK) | `STRING` | `NULL` | Source IDs are always STRING |
| Any other text attribute | `STRING` | `NULL` | Default for unknown text columns |

### Flag columns (`IS_*`)
| Pattern | Type | Nullable | Default |
|---|---|---|---|
| `IS_*` | `STRING` | `NULL` | `DEFAULT 'N'` — always add this default |

### Business date columns
| Pattern | Type | Nullable |
|---|---|---|
| `VALID_FROM`, `EFFECTIVE_DATE`, `START_DATE` | `TIMESTAMP_NTZ(6)` | `NULL` |
| `VALID_TO`, `EXPIRY_DATE`, `END_DATE` | `TIMESTAMP_NTZ(6)` | `NULL` |
| `*_DATE` (business date, not audit) | `TIMESTAMP_NTZ(6)` | `NULL` |
| `*_TS` (non-audit timestamp) | `TIMESTAMP_NTZ(6)` | `NULL` |

### Raw source mirror columns (`SOURCE_*`)
| Pattern | Type | Nullable |
|---|---|---|
| `SOURCE_*` (any) | `STRING` | `NULL` | Preserve raw source value as-is |

---

## Zone 3 — Hash Key

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `{ENTITY}_HKEY` | `STRING` | `NOT NULL` | Hash of all Zone 2 business columns for change detection |

---

## Zone 4 — Audit Columns (fixed, no variation)

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `{PREFIX}_IS_ACTIVE` | `BOOLEAN` | `NOT NULL` | SCD2 active flag; `TRUE` = current record |
| `{PREFIX}_INSERTED_TS` | `TIMESTAMP_NTZ(6)` | `NOT NULL` | Insert timestamp (microsecond precision) |
| `{PREFIX}_INSERTED_BY` | `STRING` | `NOT NULL` | User or process that inserted the record |
| `{PREFIX}_UPDATED_TS` | `TIMESTAMP_NTZ(6)` | `NOT NULL` | Last update timestamp |
| `{PREFIX}_UPDATED_BY` | `STRING` | `NOT NULL` | User or process that last updated the record |

---

## Summary Cheat Sheet

| Zone | NOT NULL rule | Type default |
|---|---|---|
| Zone 1 (keys) | **`NOT NULL`** | `LONG` for SKEYs, `STRING` for SOURCE_UNIQUE_ID |
| Zone 2 (business) | No constraint | `STRING` for most; `FLOAT` for geo/scores; `DECIMAL` for amounts; `TIMESTAMP_NTZ(6)` for dates |
| Zone 2 (`IS_*` flags) | No constraint | `STRING DEFAULT 'N'` |
| Zone 3 (hash) | **`NOT NULL`** | `STRING` |
| Zone 4 (audit) | **`NOT NULL`** | `BOOLEAN` for `{PREFIX}_IS_ACTIVE`; `TIMESTAMP_NTZ(6)` for timestamps; `STRING` for BY columns |

---

## Column COMMENTs

Every column in a Silver DDL must have a `COMMENT`. Use this convention:

| Zone | Comment template |
|---|---|
| Zone 1 SKEY (own PK) | `'Surrogate key — auto-generated sequence'` |
| Zone 1 SOURCE_UNIQUE_ID | `'Unique identifier from the source system for traceability'` |
| Zone 1 REF_{PREFIX}_SOURCE_SYSTEM_SKEY | `'FK to REF_{PREFIX}_SOURCE_SYSTEM — identifies the source system'` |
| Zone 1 parent SKEY | `'FK to {PARENT_TABLE}.{PARENT_SKEY}'` |
| Zone 2 REF_*_SKEY | `'FK to {LOOKUP_TABLE} — {brief description}'` |
| Zone 2 IS_* | `'Y/N flag — {what it indicates}'` |
| Zone 2 VALID_FROM / VALID_TO | `'Start/end of the validity period for this record'` |
| Zone 2 other | `'{Brief description of what this column represents}'` |
| Zone 3 HKEY | `'MD5/SHA2 hash of business attributes for change detection'` |
| Zone 4 `{PREFIX}_IS_ACTIVE` | `'Active flag for SCD2 — TRUE indicates current record'` |
| Zone 4 `{PREFIX}_INSERTED_TS` | `'Timestamp when record was inserted'` |
| Zone 4 `{PREFIX}_INSERTED_BY` | `'User/process that inserted the record'` |
| Zone 4 `{PREFIX}_UPDATED_TS` | `'Timestamp when record was last updated'` |
| Zone 4 `{PREFIX}_UPDATED_BY` | `'User/process that last updated the record'` |
