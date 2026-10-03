---
name: gold-model
description: >
  Gold layer Snowflake Iceberg DDL generator. Use when creating, scaffolding, or generating
  a new Gold star-schema table (FACT_ or DIM_) from a data dictionary, column spec, or Excel.
  Preserves column order exactly as provided. Applies correct data type mappings (LONG for keys,
  STRING for strings, DATE for business dates, TIMESTAMP_NTZ for system timestamps,
  NUMBER(38,5) for metrics, BOOLEAN for flags), NOT NULL constraints, Gold naming conventions,
  and Snowflake ICEBERG DDL structure. No HKEY is added to Gold tables.
  Trigger phrases: "create gold model", "scaffold gold table", "generate gold DDL", "new fact table",
  "new dim table", "gold layer", "gold DDL from spec", "gold iceberg table".
---

# Gold Model — DDL Generator Skill

## When to Use

Load this skill whenever the user asks to:
- Create or scaffold a new Gold-layer Snowflake Iceberg table (FACT_ or DIM_)
- Generate a Gold DDL from a data dictionary, column spec, or Excel sheet
- Add a new table to the Gold layer (any domain: opportunity, company, property, employee, contact, etc.)
- Review or fix the data types / nullability of an existing Gold DDL

---

## Key Differences: Gold vs. Silver

| Rule | Gold | Silver |
|---|---|---|
| Column order | **Preserve exactly as in spec/Excel** | Reordered by zone (Zone 1 → 2 → 3 → 4) |
| HKEY | **Always injected** as `{TABLE_NAME}_HKEY STRING NOT NULL` immediately before audit columns; rename source `MD5_HASH` to `{TABLE_NAME}_HKEY` if present | Always injected |
| SOURCE_UNIQUE_ID | Not present | Zone 1.2, always |
| REF_{PREFIX}_SOURCE_SYSTEM_SKEY | Not present | Zone 1.3, always |
| Table prefix | `FACT_` or `DIM_` | No prefix convention |
| Database | `{ENV}_{PREFIX}_GOLD_DB` | `{ENV}_{PREFIX}_SILVER_DB` |
| External volume | `{PREFIX}_SF_{ENV}_GOLD` | `{PREFIX}_SF_{ENV}_SILVER` |
| Base location | `{prefix_lower}-sf-{env}-gold/...` | `{prefix_lower}-sf-{env}-silver/...` |

`{PREFIX}` is the project-configurable audit-column / shared-object namespace (see Step 1 below).

---

## Procedure

Follow these steps **in order** every time:

### Step 1 — Gather inputs

Ask the user for the following if not already provided:
1. **Table name** — e.g. `FACT_OPP_PROJECT` or `DIM_CLIENT`
2. **Schema / domain** — e.g. `OPPORTUNITY`
3. **Column spec or Excel data** — paste column list with data types, nullable flags, and comments
4. **Environment** — default to `DEV` unless stated otherwise
5. **Project prefix (`{PREFIX}`)** — the short namespace used for audit columns and shared reference objects on this project (e.g. `GDP`, `DW`, `EDW`). Ask once per project; reuse for every table. If the user has no such convention, leave blank — audit columns then have no leading prefix.

### Step 2 — Preserve column order, apply type mappings

**Do NOT reorder columns.** Keep them in the exact sequence provided in the spec/Excel.

For each column, apply the data type mappings from [./references/data-types.md](./references/data-types.md):
- Surrogate Key / FK `*_SKEY` → `LONG`
- All string / text / code / name / description columns → `STRING`
- Financial / default metrics (`*_AMOUNT`, `*_VALUE`, general numeric) → `NUMBER(38,5)`
- Count metrics (`*_COUNT`, `*_QTY`, integer measures) → `NUMBER(38,0)`
- Percentages / ratios (`*_RATE`, `*_PCT`, `*_PERCENTAGE`) → `NUMBER(10,5)`
- Latitude → `NUMBER(15,8)` / Longitude → `NUMBER(15,8)`
- Business date columns (`*_DATE`) → `DATE`
- System timestamp columns (`*_TS`) → `TIMESTAMP_NTZ`
- Timezone-aware timestamps → `TIMESTAMP_TZ`
- Boolean / flag columns (`IS_*`, `{PREFIX}_IS_ACTIVE`) → `BOOLEAN`
- Legacy input types: `BIGINT`/`INT` → `LONG`; `STRING` → `STRING`; `CHAR(1)` → `STRING`; `FLOAT` → `NUMBER(38,5)`; `DECIMAL(38,6)` → `NUMBER(38,5)`; `DECIMAL(38,0)` → `NUMBER(38,0)`; `TIMESTAMP_NTZ(6)` → `TIMESTAMP_NTZ`

### Step 3 — Apply nullability

Using [./references/data-types.md](./references/data-types.md):
- Own surrogate PK (`*_SKEY` PK column) → `NOT NULL`
- FK SKEY columns (`*_SKEY` non-PK) → nullable per spec; default `NULL` if spec says `Y`; `NOT NULL` if spec says `N`
- Business columns → follow the nullable flag from spec; if unspecified default to `NULL`
- `IS_*` flag columns → `STRING DEFAULT 'N'`; nullable per spec
- `{PREFIX}_*` audit columns → always `NOT NULL`
- `{PREFIX}_IS_ACTIVE` → `BOOLEAN NOT NULL`

### Step 4 — Handle audit columns

Using [./references/data-types.md](./references/data-types.md):
- The 5 `{PREFIX}_*` audit columns must always appear **last** in the column list.
- If they are already at the end in the spec, keep them there. If absent from spec, inject them.
- **Never include a HKEY column.** If the spec contains a `*_HKEY` column, **omit it silently**.

### Step 5 — Fix naming issues

- Column names with spaces → replace space with underscore (e.g. `CLIENT CLASSIFICATION` → `CLIENT_CLASSIFICATION`). Add `-- REVIEW` comment.
- Trim trailing/leading whitespace from all column names.
- Do not rename any other columns.
- Column names ending in `_F` → rename suffix to `_FLAG` (e.g. `SENT_F` → `SENT_FLAG`). **Excludes** `ACTIVE_F` (mapped to `{PREFIX}_IS_ACTIVE`) and any `{PREFIX}_*` column.

### Step 6 — Apply DDL conventions

Using [./references/snowflake-ddl-conventions.md](./references/snowflake-ddl-conventions.md):
- Fill in [./assets/iceberg_ddl_template.sql](./assets/iceberg_ddl_template.sql)
- Replace all `{PLACEHOLDER}` tokens with actual values (including `{PREFIX}` from Step 1)
- Add a `COMMENT` to every column
- Add the table-level `COMMENT`
- Add the `CONSTRAINT PK_{TABLE_NAME}` PRIMARY KEY line
- Add `CONSTRAINT UK_{TABLE_NAME}_{COLUMN}` UNIQUE lines for all UK columns
- Add `CONSTRAINT FK_{TABLE_NAME}_{FK_COLUMN_NAME}` FOREIGN KEY lines for every FK SKEY column (where `{FK_COLUMN_NAME}` is the full FK SKEY column name, e.g. `FK_DIM_CAMPAIGN_DIM_DATA_SOURCE_SKEY`), referencing the correct DIM table in the same schema
- Add the ICEBERG footer (`EXTERNAL_VOLUME`, `ICEBERG_VERSION`, `CATALOG`, `BASE_LOCATION`)

### Step 7 — Emit and explain

Output the complete DDL, then provide a brief summary table:

| Section | Columns included |
|---|---|
| Surrogate key | own `*_SKEY` |
| FK keys | any `*_SKEY` FK columns |
| Business columns | all remaining domain columns in spec order |
| Audit | `{PREFIX}_IS_ACTIVE`, `{PREFIX}_INSERTED_TS`, `{PREFIX}_INSERTED_BY`, `{PREFIX}_UPDATED_TS`, `{PREFIX}_UPDATED_BY` |

Flag any columns with naming fixes or data type overrides using `-- REVIEW` comments.

---

## Key Rules (Quick Reference)

| Rule | Detail |
|---|---|
| Column order is preserved from spec | Do NOT reorder. Only audit columns are moved to end if out of position |
| HKEY mandatory | Always inject `{TABLE_NAME}_HKEY STRING NOT NULL` directly above the audit-column block; rename source `MD5_HASH` → `{TABLE_NAME}_HKEY` |
| No SOURCE_UNIQUE_ID | Not a Gold column — omit if present in spec |
| No REF_{PREFIX}_SOURCE_SYSTEM_SKEY | Not a Gold column — omit if present in spec |
| IS_* flags always get DEFAULT FALSE | Stored as `BOOLEAN DEFAULT FALSE`; never STRING |
| All audit columns are NOT NULL | `{PREFIX}_IS_ACTIVE` BOOLEAN, timestamps TIMESTAMP_NTZ, BY columns STRING |
| BIGINT / INT / LONG → LONG | All integer key types normalised to LONG |
| STRING / CHAR(1) → STRING | All text types use Snowflake native STRING (VARCHAR(16777216)) |
| DATE → DATE | Business dates stay as DATE |
| TIMESTAMP_NTZ(6) → TIMESTAMP_NTZ | Drop precision on system timestamps |
| FLOAT / DECIMAL → NUMBER | Normalise to NUMBER(38,5), NUMBER(38,0), NUMBER(10,5), or NUMBER(15,8) per column role |
| DEV is the default environment | Unless user specifies QA/UAT/PROD |
| Every column needs COMMENT | No column without a COMMENT clause |
| Spaces in column names → underscore | With REVIEW flag |
| Column names ending in `_F` → `_FLAG` | Excludes `ACTIVE_F` and `{PREFIX}_*` columns |
| FK constraint name | `FK_{TABLE_NAME}_{FK_COLUMN_NAME}` — uses full FK SKEY column name |

---

## Column Naming Conventions (Gold)

| Pattern | Meaning | Notes |
|---|---|---|
| `FACT_{ENTITY}_SKEY` | Fact table surrogate PK | Own PK |
| `DIM_{ENTITY}_SKEY` | Dimension table surrogate PK | Own PK |
| `{SOMETHING}_DIM_SKEY` | FK to a dimension table | e.g. `OPPORTUNITY_STAGE_DIM_SKEY` |
| `DATE_{ROLE}_DIM_SKEY` | Role-playing FK to DIM_DATE | e.g. `DATE_PROJECT_START_DIM_SKEY` |
| `{TABLE_NAME}_HKEY` | Hash key — `STRING NOT NULL`; positioned directly above audit columns; rename of source `MD5_HASH` if present | Always present |
| `{PREFIX}_*` | Audit columns (project-configurable prefix) | Zone 4 — always last |
| `IS_*` | Boolean flag (stored as BOOLEAN) | DEFAULT FALSE always |
| `SEQ_{TABLE_NAME}_SKEY` | Snowflake sequence for SKEY | Emitted before CREATE TABLE |
| `PK_{TABLE_NAME}` | Primary key constraint | Constraint line |
| `FK_{TABLE_NAME}_{FK_COLUMN_NAME}` | Foreign key constraint name — uses full FK SKEY column name (e.g. `FK_DIM_CAMPAIGN_DIM_DATA_SOURCE_SKEY`) | Constraint line |

---

## Reference Files

- [Column classification and ordering rules](./references/column-classification.md)
- [Data types and nullability](./references/data-types.md)
- [Snowflake DDL conventions, ICEBERG options, formatting](./references/snowflake-ddl-conventions.md)
- [Annotated DDL template](./assets/iceberg_ddl_template.sql)
