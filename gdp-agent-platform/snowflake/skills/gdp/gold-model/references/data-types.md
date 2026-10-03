# Gold — Data Types & Nullability Rules

Apply these rules after identifying each column's role using `./column-classification.md`. `{PREFIX}` is the project-configurable audit-column namespace gathered in Step 1 of SKILL.md (may be blank).

---

## Canonical Gold Data Types

The following canonical types define what is used in every Gold DDL. Apply these regardless of what the source spec says:

| Column Category | Gold DDL Type | Notes |
|---|---|---|
| Surrogate Key (`*_SKEY` PK) | `LONG` | Own surrogate PK |
| Foreign Key (`*_SKEY` FK) | `LONG` | FK references to dimension tables |
| Reference Key | `LONG` | Any other reference/lookup key |
| Date Key (`DATE_*_DIM_SKEY`) | `LONG` | FK to DIM_DATE role-playing keys |
| Source Unique ID | `STRING` | Not present in Gold — omit if seen in spec |
| All String Columns | `STRING` | Names, codes, descriptions, IDs, text |
| Default Metrics | `NUMBER(38,5)` | General numeric measures |
| Financial Amounts (`*_AMOUNT`, `*_VALUE`) | `NUMBER(38,5)` | Monetary / financial figures |
| Count Metrics (`*_COUNT`, `*_QTY`) | `NUMBER(38,0)` | Integer counts and durations |
| Percentage / Ratios (`*_RATE`, `*_PCT`) | `NUMBER(10,5)` | Rates, percentages, ratios |
| Latitude | `NUMBER(15,8)` | Geospatial latitude |
| Longitude | `NUMBER(15,8)` | Geospatial longitude |
| Business Date (`*_DATE`) | `DATE` | Business-facing date columns |
| System Timestamp (`*_TS`, audit) | `TIMESTAMP_NTZ` | Timezone-free system timestamps |
| Timezone Timestamp | `TIMESTAMP_TZ` | Timezone-aware timestamps |
| Boolean / Flag (`IS_*`, `{PREFIX}_IS_ACTIVE`) | `BOOLEAN` | True/false flags |

### Source Type Normalisation (legacy inputs from spec)

| Source type in spec | Gold DDL type | Notes |
|---|---|---|
| `BIGINT` | `LONG` | Normalise to LONG |
| `LONG` | `LONG` | Keep as-is |
| `INT`, `INTEGER` | `LONG` | Normalise to LONG |
| `DATE` | `DATE` | Keep as DATE |
| `CHAR(1)` | `STRING` | Single-char flags standardised to STRING |
| `BOOLEAN` | `BOOLEAN` | Keep as-is |
| `FLOAT` | `NUMBER(38,5)` | Normalise to default metric type |
| `STRING` | `STRING` | Keep as-is |
| `DECIMAL(38,0)` | `NUMBER(38,0)` | Normalise to NUMBER |
| `DECIMAL(38,6)` | `NUMBER(38,5)` | Normalise to NUMBER(38,5) |
| `TIMESTAMP_NTZ(6)` | `TIMESTAMP_NTZ` | Drop precision |
| `TIMESTAMP_NTZ` | `TIMESTAMP_NTZ` | Keep as-is |

---

## Surrogate Key (PK SKEY)

| Pattern | Type | Nullable | Notes |
|---|---|---|---|
| Own PK `*_SKEY` | `LONG` | `NOT NULL` | No DEFAULT in DDL — NEXTVAL assigned in dbt SELECT |

---

## Foreign Key SKEY Columns

| Pattern | Type | Nullable | Notes |
|---|---|---|---|
| FK `*_SKEY` (spec nullable = Y) | `LONG` | `NULL` (omit keyword) | |
| FK `*_SKEY` (spec nullable = N) | `LONG` | `NOT NULL` | |

---

## Business Columns

### Text / String Attributes
| Pattern / Name | Type | Nullable | Notes |
|---|---|---|---|
| `*_NAME`, `*_TITLE` | `STRING` | per spec | |
| `*_CODE` | `STRING` | per spec | |
| `*_ID` (non-PK) | `STRING` | per spec | Source IDs are STRING even if spec says BIGINT/LONG |
| `*_DESC`, `*_DESCRIPTION` | `STRING` | per spec | |
| `*_TYPE` | `STRING` | per spec | |
| `*_STATUS` | `STRING` | per spec | |
| `*_URL`, `*_URI` | `STRING` | per spec | |
| Any other text attribute | `STRING` | per spec | Default for unknown text |

### Numeric Measures
| Pattern | Type | Nullable | Notes |
|---|---|---|---|
| `*_AMOUNT`, `*_VALUE` | `NUMBER(38,5)` | per spec | Financial / monetary amounts |
| `*_COUNT`, `*_DURATION_DAYS`, `*_QTY` | `NUMBER(38,0)` | per spec | Integer counts |
| `*_RATE`, `*_PCT`, `*_PERCENTAGE` | `NUMBER(10,5)` | per spec | Percentages / ratios |
| `*_LATITUDE`, `LAT` | `NUMBER(15,8)` | per spec | Geospatial latitude |
| `*_LONGITUDE`, `LNG`, `LON` | `NUMBER(15,8)` | per spec | Geospatial longitude |
| General / default numeric measure | `NUMBER(38,5)` | per spec | Default for unclassified measures |
| Spec says `FLOAT` | `NUMBER(38,5)` | per spec | Normalise to NUMBER(38,5) |
| Spec says `DECIMAL(38,0)` | `NUMBER(38,0)` | per spec | Normalise to NUMBER |
| Spec says `DECIMAL(38,6)` | `NUMBER(38,5)` | per spec | Normalise to NUMBER(38,5) |

### Flag Columns
| Pattern | Type | Nullable | Default |
|---|---|---|---|
| `IS_*` | `BOOLEAN` | per spec | Always add `DEFAULT FALSE` |

### Date / Timestamp Columns
| Pattern | Type | Nullable |
|---|---|---|
| `*_DATE`, `EXPIRY_DATE`, `VALID_FROM`, `VALID_TO` | `DATE` | per spec |
| `*_TS` (system timestamps) | `TIMESTAMP_NTZ` | per spec |
| Timezone-aware timestamps | `TIMESTAMP_TZ` | per spec |

### Special Pattern: Non-PK IDs that look numeric
| Rule | Type |
|---|---|
| If spec says `BIGINT`/`LONG`/`DECIMAL` AND column name ends with `_ID` (not `_SKEY`) | `STRING` — source IDs are always stored as STRING |
| Examples: `PS_PROJECT_ID`, `STRATEGIC_PARTNER_ID` | `STRING` |

---

## Zone 4 — Audit Columns (fixed, Gold standard)

These 5 columns are **always last**, always `NOT NULL`, always in this exact order:

| Column | Type | Constraint | Notes |
|---|---|---|---|
| `{PREFIX}_IS_ACTIVE` | `BOOLEAN` | `NOT NULL` | SCD2 active flag; `TRUE` = current record |
| `{PREFIX}_INSERTED_TS` | `TIMESTAMP_NTZ` | `NOT NULL` | Insert timestamp |
| `{PREFIX}_INSERTED_BY` | `STRING` | `NOT NULL` | User or process that inserted the record |
| `{PREFIX}_UPDATED_TS` | `TIMESTAMP_NTZ` | `NOT NULL` | Last update timestamp |
| `{PREFIX}_UPDATED_BY` | `STRING` | `NOT NULL` | User or process that last updated the record |

> If the spec defines `{PREFIX}_IS_ACTIVE` as `STRING` — override to `BOOLEAN` in the DDL.

---

## Nullability from Spec

When the spec includes a Nullable column (`Y` / `N`):
- `N` → add `NOT NULL` (except audit columns which are always NOT NULL regardless)
- `Y` → no constraint keyword (Snowflake columns are nullable by default)
- If no nullable flag in spec → default to nullable (no keyword)

**Exception:** `{PREFIX}_*` audit columns are always `NOT NULL` regardless of spec.

---

## Summary Cheat Sheet

| Column role | NOT NULL rule | Type default |
|---|---|---|
| Own PK SKEY | **`NOT NULL`** | `LONG` |
| FK SKEY | Per spec | `LONG` |
| Business text/code/ID | Per spec | `STRING` |
| IS_* flag | Per spec | `BOOLEAN DEFAULT FALSE` |
| Business date | Per spec | `DATE` |
| System timestamp | Per spec | `TIMESTAMP_NTZ` |
| Numeric measure | Per spec | `NUMBER(38,5)` |
| `{PREFIX}_*` audit | **`NOT NULL`** | See Zone 4 table above |
| HKEY | **OMIT** | — |
