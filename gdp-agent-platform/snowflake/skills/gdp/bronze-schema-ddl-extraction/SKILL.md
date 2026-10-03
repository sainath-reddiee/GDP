---
name: bronze-schema-ddl-extraction
description: >
  Extract and validate DDL from Bronze layer Snowflake schemas. Ensures all tables are in
  proper Iceberg format with required audit fields ({PREFIX}_INSERTED_TS, {PREFIX}_IS_ACTIVE, {PREFIX}_ROW_HASH,
  {PREFIX}_UPDATED_TS, ETL_CREATED_TS). Generates enterprise-grade deployment-ready SQL scripts with
  dynamic ${ENV_NAME} placeholders, CREATE IF NOT EXISTS syntax, and faithful metadata extraction
  — excluding _HIST tables. Trigger phrases: "extract bronze DDL", "bronze schema DDL",
  "bronze deployment script", "validate bronze tables", "bronze iceberg DDL", "bronze extraction".
---

# Bronze Schema DDL Extraction

## When to Use

Load this skill whenever the user asks to:
- Extract DDLs from a Bronze schema
- Generate a consolidated deployment SQL script for Bronze tables
- Validate Bronze tables against Bronze layer standards
- Review Bronze table compliance (Iceberg format, audit fields)

Ask the user once for the project's audit-column namespace (`{PREFIX}`, e.g. `GDP`, `DW`, `EDW`; may be blank) if not already established — it drives every audit-field name below.

---

## Key Rules

### Rule 1 — All tables must be Iceberg format

Every table in a Bronze schema MUST be an Iceberg table with:
- `EXTERNAL_VOLUME` specified
- `CATALOG = 'SNOWFLAKE'`
- `BASE_LOCATION` pointing to the correct Bronze storage path

If a table is NOT Iceberg, flag it as a compliance violation.

### Rule 2 — Required Audit Fields

Every Bronze table MUST contain these 5 audit columns:

| Column | Type |
|---|---|
| `{PREFIX}_INSERTED_TS` | `TIMESTAMP_NTZ(6)` |
| `{PREFIX}_IS_ACTIVE` | `BOOLEAN` |
| `{PREFIX}_ROW_HASH` | `STRING` or `BINARY` |
| `{PREFIX}_UPDATED_TS` | `TIMESTAMP_NTZ(6)` |
| `ETL_CREATED_TS` | `TIMESTAMP_NTZ(6)` |

**IMPORTANT — Faithful extraction rule**: When generating deployment DDL, preserve audit columns **EXACTLY as they exist in the actual source table** — same type, same nullability. Do NOT alter NULLABLE to NOT NULL. Do NOT change BINARY to STRING. The deployment script must be a faithful representation of the source DDL. Flag nullability issues in the compliance report only — never silently fix them in the output DDL.

### Rule 3 — Exclude _HIST tables from deployment scripts

- **DO NOT include** any table whose name ends with `_HIST`
- History tables are managed separately and must not appear in the consolidated deployment script

### Rule 4 — Column ordering

1. **Source columns** — preserve exact source order
2. **Audit columns** — preserve exact source position (do not reorder)

### Rule 5 — Data types

Bronze layer preserves source types exactly — never normalize types in Bronze.

### Rule 6 — Naming conventions

| Pattern | Meaning |
|---|---|
| `{PREFIX}_*` | Project-managed audit columns |
| `ETL_*` | ETL pipeline metadata columns |
| `*_HIST` | History table (excluded from deployment scripts) |

---

## Deployment Formatting Standards (Enterprise Style)

### Rule 7 — Dynamic Environment Placeholders (MANDATORY)

**ALL** database names, storage paths, and external volumes MUST use `${ENV_NAME}` placeholders. **NEVER hardcode** DEV/QA/UAT/PROD.

| Pattern | Placeholder Format |
|---|---|
| Database name | `${ENV_NAME}_{PREFIX}_BRONZE_DB` |
| External volume | `{PREFIX}_SF_${ENV_NAME}_BRONZE` |
| Base location path | `{prefix_lower}-sf-${ENV_NAME}-bronze/{SCHEMA}/{TABLE_NAME}/` |

### Rule 8 — CREATE IF NOT EXISTS (NOT CREATE OR REPLACE)

```sql
CREATE ICEBERG TABLE IF NOT EXISTS ${ENV_NAME}_{PREFIX}_BRONZE_DB.{SCHEMA}.{TABLE_NAME} (
```

**NEVER generate** `CREATE OR REPLACE`.

### Rule 9 — NO USE WAREHOUSE statements

### Rule 10 — NO CREATE SCHEMA statements

### Rule 11 — Comment Handling (METADATA-ONLY — NO AI GENERATION)

**CRITICAL — Comments must come ONLY from actual Snowflake metadata.**

Sources of truth for comments:
- `GET_DDL()` output
- `INFORMATION_SCHEMA.COLUMNS.COMMENT` column
- `DESCRIBE TABLE` comment field

**Rules:**
1. If a column has a COMMENT in the Snowflake metadata → include it verbatim in the DDL
2. If a column has NO COMMENT in the Snowflake metadata → do NOT generate a COMMENT clause
3. If a table has a COMMENT in the Snowflake metadata → include it as `COMMENT = '...'`
4. If a table has NO COMMENT → do NOT generate a COMMENT clause

**NEVER do any of the following:**
- Infer or generate column descriptions from column names
- Profile column values to write business meaning
- Generate semantic summaries or narrative explanations
- Add comments like "This column likely represents...", "A unique identifier for...", "Contains values such as..."
- Enrich metadata beyond what exists in the source

**The DDL must reflect source Snowflake metadata exactly — nothing added, nothing removed.**

### Rule 12 — NO empty object sections

If no sequences/views/tasks/stages/pipes/file formats exist, completely omit those sections.

### Rule 13 — Section Separators

Header:
```sql
-- ============================================================================
-- ${ENV_NAME}_{PREFIX}_BRONZE_DB.{SCHEMA} - Complete Object DDLs
-- ============================================================================
```

Table sub-sections:
```sql
-- ----------------------------------------------------------------------------
-- TABLE: {TABLE_NAME}
-- ----------------------------------------------------------------------------
```

### Rule 14 — Iceberg Footer Format

```sql
    EXTERNAL_VOLUME = '{PREFIX}_SF_${ENV_NAME}_BRONZE'
    CATALOG = 'SNOWFLAKE'
    BASE_LOCATION = '{prefix_lower}-sf-${ENV_NAME}-bronze/{SCHEMA}/{TABLE_NAME}/';
```

### Rule 15 — Deployment-ready for any environment

The generated SQL file must work for ANY environment (DEV, QA, UAT, PROD) by simply setting the `${ENV_NAME}` variable.

---

## Procedure

### Step 1 — Inventory the source schema

```sql
SHOW TABLES IN SCHEMA <DB>.<SCHEMA>;
SHOW SEQUENCES IN SCHEMA <DB>.<SCHEMA>;
SHOW VIEWS IN SCHEMA <DB>.<SCHEMA>;
SHOW STREAMS IN SCHEMA <DB>.<SCHEMA>;
SHOW TASKS IN SCHEMA <DB>.<SCHEMA>;
SHOW FILE FORMATS IN SCHEMA <DB>.<SCHEMA>;
SHOW STAGES IN SCHEMA <DB>.<SCHEMA>;
SHOW PIPES IN SCHEMA <DB>.<SCHEMA>;
```

Categorize each table:
- **Core tables** (Iceberg, not _HIST) → include in deployment script
- **_HIST tables** → note in inventory, exclude from .sql output
- **Non-Iceberg tables** → flag as compliance violation

### Step 2 — Validate each table

For each core table, check:
1. ✅ Is it an Iceberg table?
2. ✅ Does it have all 5 required audit fields?
3. ✅ Are audit fields in the correct position (last columns)?

Report issues in compliance report — do NOT alter the DDL.

### Step 3 — Extract DDL (with robust comment preservation for large tables)

**Primary source**: Use `GET_DDL('TABLE', '<DB>.<SCHEMA>.<NAME>', TRUE)` for each core table.

**CRITICAL — Truncation handling for wide tables (e.g., 100+ columns):**

`GET_DDL()` output is frequently truncated for large tables. When truncation is detected (look for `(Some row/cell values were truncated...)` or incomplete DDL), you MUST:

1. **Always query `INFORMATION_SCHEMA.COLUMNS` with the COMMENT field** to get complete column metadata:
   ```sql
   SELECT COLUMN_NAME, DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE,
          IS_NULLABLE, COLUMN_DEFAULT, COMMENT, ORDINAL_POSITION
   FROM <DB>.INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = '<SCHEMA>' AND TABLE_NAME = '<TABLE>'
   ORDER BY ORDINAL_POSITION;
   ```

2. **For tables with many columns (100+)**, proactively query INFORMATION_SCHEMA regardless of whether GET_DDL appears truncated — GET_DDL may silently drop COMMENT clauses on later columns even when the structure appears complete.

3. **Merge strategy**: Use GET_DDL for the Iceberg footer and table structure, but always use INFORMATION_SCHEMA as the authoritative source for column-level COMMENTs.

**Comment handling rules:**
- If `INFORMATION_SCHEMA.COLUMNS.COMMENT` is non-NULL and non-empty for a column → include `COMMENT '...'` verbatim
- If `INFORMATION_SCHEMA.COLUMNS.COMMENT` is NULL or empty → emit column WITHOUT a COMMENT clause
- **NEVER skip comments for columns beyond a certain position** — process ALL columns regardless of table width
- **NEVER truncate or summarize comment text** — preserve full verbatim content even if very long
- Comments must be sourced ONLY from Snowflake metadata — never inferred or generated

### Step 4 — Generate deployment script

Assemble all DDL into a single `.sql` file:

1. Header line with `${ENV_NAME}_{PREFIX}_BRONZE_DB.{SCHEMA} - Complete Object DDLs`
2. Tables section (only if tables exist)
3. Other object sections (only if they exist)

**DO NOT include:**
- `USE WAREHOUSE`
- `CREATE SCHEMA`
- Empty sections
- `CREATE OR REPLACE`
- Hardcoded environment names
- AI-generated/inferred comments

### Step 5 — Produce compliance report

Output a summary showing violations found.

---

## Reference Files

- [Bronze audit field rules](./references/bronze-audit-rules.md)
- [Bronze Iceberg conventions](./references/bronze-iceberg-conventions.md)
- [Deployment formatting standards](./references/deployment-formatting.md)
- [DDL template](./assets/bronze_ddl_template.sql)
