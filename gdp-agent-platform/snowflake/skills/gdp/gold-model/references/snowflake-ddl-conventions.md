# Gold — Snowflake DDL Conventions

`{PREFIX}` below is the project-configurable audit-column / shared-object namespace gathered in Step 1 of SKILL.md (e.g. `GDP`, `DW`; may be blank).

---

## Table Type

All Gold tables are **Snowflake Iceberg tables** using the internal Snowflake catalog.

```sql
CREATE OR REPLACE ICEBERG TABLE {DATABASE}.{SCHEMA}.{TABLE_NAME} (
  ...
)
COMMENT = '{table description}'
EXTERNAL_VOLUME = '{external_volume}'
ICEBERG_VERSION = 2
CATALOG = 'SNOWFLAKE'
BASE_LOCATION = '{base_location_path}';
```

---

## Environment-Aware Database Names

| Environment | Database |
|---|---|
| DEV | `DEV_{PREFIX}_GOLD_DB` |
| QA | `QA_{PREFIX}_GOLD_DB` |
| UAT | `UAT_{PREFIX}_GOLD_DB` |
| PROD | `PROD_{PREFIX}_GOLD_DB` |

Default to `DEV_{PREFIX}_GOLD_DB` unless the user specifies otherwise.

---

## Schema Naming

Schema = the domain the table belongs to (uppercase noun). Examples:

| Domain | Schema |
|---|---|
| Opportunity star schema | `OPPORTUNITY` |
| Company star schema | `COMPANY` |
| Property star schema | `PROPERTY` |
| Employee star schema | `EMPLOYEE` |
| Contact star schema | `CONTACT` |
| Shared dimensions | `SHARED` |

---

## Table Naming Conventions

| Table type | Prefix | Example |
|---|---|---|
| Fact table | `FACT_` | `FACT_OPP_PROJECT` |
| Dimension table | `DIM_` | `DIM_CLIENT`, `DIM_COUNTRY` |

---

## Surrogate Key Sequence

Every Gold table has its own Snowflake sequence for the surrogate key. **Always emit the sequence creation statement immediately before the `CREATE OR REPLACE ICEBERG TABLE` statement.**

**Sequence naming pattern:**
```
{DATABASE}.{SCHEMA}.SEQ_{TABLE_NAME}_SKEY
```

**Sequence DDL:**
```sql
CREATE OR REPLACE SEQUENCE {DATABASE}.{SCHEMA}.SEQ_{TABLE_NAME}_SKEY
    START WITH 1
    INCREMENT BY 1
    NOORDER;
```

**DDL column definition (NOT NULL, no DEFAULT):**
```sql
{OWN_SKEY_COLUMN}  LONG  NOT NULL  COMMENT 'Surrogate key — auto-generated sequence',
```

> **Note:** All string columns in Gold DDL use Snowflake native `STRING` type (alias for `VARCHAR(16777216)` — max 16MB). Do NOT use `VARCHAR(MAX)` — that is T-SQL syntax and is not valid in Snowflake.

**Rules:**
- The PK SKEY column carries `NOT NULL` in the DDL
- No `DEFAULT ... NEXTVAL` in the DDL — NEXTVAL is assigned in the dbt SELECT
- The sequence object must be created before the table

---

## Primary Key Constraint

Always add an explicit PK constraint as the last item in the column list (after audit columns):

```sql
CONSTRAINT PK_{TABLE_NAME} PRIMARY KEY ({OWN_SKEY_COLUMN})
```

Where `{OWN_SKEY_COLUMN}` is the actual PK column name (e.g. `DIM_CLIENT_SKEY`, `OPPORTUNITY_STAGE_DIM_SKEY`).

---

## Foreign Key Constraints

For every FK SKEY column on the table, emit an explicit FK constraint:

```sql
CONSTRAINT FK_{TABLE_NAME}_{FK_COLUMN_NAME} FOREIGN KEY ({FK_COLUMN_NAME})
    REFERENCES {DATABASE}.{SCHEMA}.{TARGET_TABLE} ({TARGET_PK_COLUMN})
```

- `{FK_COLUMN_NAME}` is the **full FK SKEY column name** including the `_SKEY` suffix.
- Example: column `DIM_DATA_SOURCE_SKEY` on `DIM_CAMPAIGN` → constraint name `FK_DIM_CAMPAIGN_DIM_DATA_SOURCE_SKEY`.
- Always use the FK column name (not the target table name) so names stay unique when multiple FKs reference the same target (e.g. role-playing `DATE_*_DIM_SKEY`).

---

## Column Name Fixes

| Issue | Fix |
|---|---|
| Spaces in column name | Replace space with `_` |
| Leading/trailing whitespace | Trim silently |
| Column name ends with `_F` | Rename suffix `_F` to `_FLAG` (e.g. `SENT_F` → `SENT_FLAG`). **Excludes** `ACTIVE_F` (mapped to `{PREFIX}_IS_ACTIVE`) and any `{PREFIX}_*` column. |

---

## ICEBERG Footer Options

| Option | Value pattern | Example (DEV, `{PREFIX}` = `GDP`) |
|---|---|---|
| `EXTERNAL_VOLUME` | `'{PREFIX}_SF_{ENV}_GOLD'` | `'GDP_SF_DEV_GOLD'` |
| `ICEBERG_VERSION` | `2` | `2` |
| `CATALOG` | `'SNOWFLAKE'` | `'SNOWFLAKE'` |
| `BASE_LOCATION` | `'{prefix_lower}-sf-{env}-gold/{schema_lower}/{TABLE_NAME}/'` | `'gdp-sf-dev-gold/opportunity/DIM_CLIENT/'` |

**ENV mapping:**

| Environment | ENV token (upper) | env token (lower) |
|---|---|---|
| DEV | `DEV` | `dev` |
| QA | `QA` | `qa` |
| UAT | `UAT` | `uat` |
| PROD | `PROD` | `prod` |

---

## Column Formatting Rules

- Align column names, types, and constraints using spaces for readability
- Column name width: pad to ~42 characters
- Type width: pad to ~20 characters
- Constraint (`NOT NULL`, `DEFAULT 'N'`) follows the type
- `COMMENT '...'` always at the end of the column definition
- Zone separator comments are optional for Gold (since column order is preserved); add only if helpful

---

## Table COMMENT

The table-level `COMMENT` should concisely describe:
1. The layer (Gold) and table type (dimension / fact)
2. What this entity represents
3. Primary Silver source (if applicable)
4. Whether it supports SCD Type 2 history

Pattern:
```
'Gold layer {dimension/fact} table for {entity description} — sourced from {Silver source}. Supports SCD Type 2 history.'
```

---

## Hash Key (HKEY) Column

Every Gold table includes a hash key column positioned **directly above** the audit-column block.

```sql
{TABLE_NAME}_HKEY               STRING              NOT NULL  COMMENT 'Hash key — MD5 of business columns',
```

Rules:
- Always present — inject if absent from the spec.
- If the source table has a `MD5_HASH` (or similar single hash) column, **rename it** to `{TABLE_NAME}_HKEY` rather than adding a separate column.
- Always `STRING NOT NULL`.
- Position: immediately before `{PREFIX}_IS_ACTIVE`.

---

## No Silver-specific columns in Gold

The following columns are Silver-only and must never appear in a Gold DDL:
- `SOURCE_UNIQUE_ID`
- `REF_{PREFIX}_SOURCE_SYSTEM_SKEY`

If present in the spec, omit them silently.
