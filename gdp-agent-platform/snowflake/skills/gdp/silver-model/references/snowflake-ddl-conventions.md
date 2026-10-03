# Silver — Snowflake DDL Conventions

`{PREFIX}` below is the project-configurable audit-column / shared-object namespace gathered in Step 1 of SKILL.md (e.g. `GDP`, `DW`; may be blank).

---

## Table Type

All Silver tables are **Snowflake Iceberg tables** using internal Snowflake catalog. Never use `CREATE TABLE` alone.

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

| Environment | Database prefix |
|---|---|
| DEV | `DEV_{PREFIX}_SILVER_DB` |
| QA | `QA_{PREFIX}_SILVER_DB` |
| UAT | `UAT_{PREFIX}_SILVER_DB` |
| PROD | `PROD_{PREFIX}_SILVER_DB` |

Default to `DEV_{PREFIX}_SILVER_DB` unless the user specifies otherwise.

---

## Schema Naming

Schema = the domain (plural noun) the entity belongs to. Examples:

| Domain | Schema |
|---|---|
| Company entities | `COMPANY` |
| Property entities | `PROPERTY` |
| Opportunity entities | `OPPORTUNITY` |
| Employee entities | `EMPLOYEE` |
| Contact entities | `CONTACT` |
| Shared / reference | `SHARED` |

---

## Surrogate Key Sequence

Every table has its own Snowflake sequence for the surrogate key. **Always emit the sequence creation statement immediately before the `CREATE OR REPLACE ICEBERG TABLE` statement.**

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
{TABLE_NAME}_SKEY  LONG  NOT NULL  COMMENT 'Surrogate key — auto-generated sequence',
```

**Rules:**
- Zone 1 SKEY column carries `NOT NULL` in the DDL
- No `DEFAULT ... NEXTVAL` in the DDL — NEXTVAL is assigned in the dbt SQL model's final SELECT as `{{ this.schema }}.seq_{table_name_lower}_skey.NEXTVAL::STRING`
- The sequence object must be created before the table runs for the first time
- Applies to **every** table — both core entity tables and REF_ reference tables

---

## Primary Key Constraint

Always add an explicit PK constraint at the end of the column list:

```sql
CONSTRAINT PK_{TABLE_NAME} PRIMARY KEY ({TABLE_NAME}_SKEY)
```

---

## Foreign Key Constraints

Add explicit foreign key constraints for every resolved relationship column after the PK constraint.

**When to emit an FK constraint:**
- `{PARENT_ENTITY}_SKEY` columns in Zone 1 that point to another Silver core/entity table
- `REF_*_SKEY` columns in Zone 2 when the reference table name can be resolved confidently
- `REF_{PREFIX}_SOURCE_SYSTEM_SKEY` whenever that shared reference exists in the repo/spec

**Constraint naming pattern:**
```sql
CONSTRAINT FK_{TABLE_NAME}_{TARGET_TABLE} FOREIGN KEY ({COLUMN_NAME}) REFERENCES {DATABASE}.{TARGET_SCHEMA}.{TARGET_TABLE} ({TARGET_KEY_COLUMN})
```

**Target resolution rules:**
- `{PARENT_ENTITY}_SKEY` → reference `{DATABASE}.{PARENT_SCHEMA}.{PARENT_ENTITY}` on `{PARENT_ENTITY}_SKEY`
- `REF_{PREFIX}_SOURCE_SYSTEM_SKEY` → reference the resolved `REF_{PREFIX}_SOURCE_SYSTEM` table on `{PREFIX}_SOURCE_SYSTEM_SKEY`
- `REF_{LOOKUP}_SKEY` → default to `{DATABASE}.{SCHEMA}.REF_{LOOKUP}` on `{LOOKUP}_SKEY` when the repo/spec supports that naming
- If the target table, target schema, or target key cannot be confirmed from the ER, repo, or established naming convention, do not guess silently; emit a `-- REVIEW: confirm FK target for {COLUMN_NAME}` note next to the constraint block or in the explanation

**Constraint block example (using `{PREFIX}` = `GDP` as an example):**
```sql
CONSTRAINT PK_OPPORTUNITY_CORE PRIMARY KEY (OPPORTUNITY_CORE_SKEY),
CONSTRAINT FK_OPPORTUNITY_CORE_REF_GDP_SOURCE_SYSTEM FOREIGN KEY (REF_GDP_SOURCE_SYSTEM_SKEY) REFERENCES DEV_GDP_SILVER_DB.SHARED.REF_GDP_SOURCE_SYSTEM (GDP_SOURCE_SYSTEM_SKEY),
CONSTRAINT FK_OPPORTUNITY_CORE_COMPANY_CORE FOREIGN KEY (COMPANY_CORE_SKEY) REFERENCES DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE (COMPANY_CORE_SKEY),
CONSTRAINT FK_OPPORTUNITY_CORE_REF_OPPORTUNITY_STATUS FOREIGN KEY (REF_OPPORTUNITY_STATUS_SKEY) REFERENCES DEV_GDP_SILVER_DB.OPPORTUNITY.REF_OPPORTUNITY_STATUS (OPPORTUNITY_STATUS_SKEY)
```

---

## Column Name Fixes

| Issue | Fix |
|---|---|
| Spaces in column name | Replace space with `_` |
| Leading/trailing whitespace | Trim silently |
| Column name ends with `_F` | Rename suffix `_F` to `_FLAG` (e.g. `SENT_F` → `SENT_FLAG`). **Excludes** `ACTIVE_F` (mapped to `{PREFIX}_IS_ACTIVE`) and any `{PREFIX}_*` column. |

---

## ICEBERG Footer Options

| Option | Value pattern | Notes |
|---|---|---|
| `EXTERNAL_VOLUME` | `'{PREFIX}_SF_{ENV}_SILVER'` | e.g. `'GDP_SF_DEV_SILVER'` |
| `ICEBERG_VERSION` | `2` | Always 2 |
| `CATALOG` | `'SNOWFLAKE'` | Always Snowflake internal catalog |
| `BASE_LOCATION` | `'{prefix_lower}-sf-{env}-silver/{schema_lower}/{TABLE_NAME}/'` | e.g. `'gdp-sf-dev-silver/company/COMPANY_CORE/'` |

**ENV mapping for EXTERNAL_VOLUME and BASE_LOCATION:**

| Environment | ENV token |
|---|---|
| DEV | `DEV` / `dev` |
| QA | `QA` / `qa` |
| UAT | `UAT` / `uat` |
| PROD | `PROD` / `prod` |

---

## Table COMMENT

The table-level `COMMENT` should concisely describe:
1. What this entity represents
2. Primary source system (if applicable)
3. Whether it supports SCD Type 2 history

Pattern:
```
'{Entity description} — {primary source} as primary source. Supports SCD Type 2 history.'
```

Example:
```
COMMENT='Core company entity — D&B as primary source for identity. Supports SCD Type 2 history.'
```

---

## Column Formatting Rules

1. Column names are always `UPPER_CASE`
2. Align type declarations — use consistent tab/space indentation
3. Every column is declared on **a single line**: `COLUMN_NAME  TYPE  [CONSTRAINT]  COMMENT 'text',`
4. The `COMMENT` clause is **always on the same line** as the column — never on a new line
5. Every column ends with a comma (except the last column / constraint line)
6. Separate the four zones with a blank line (no zone comment headers in output)
7. All `_SKEY` columns use `LONG` data type (not `DECIMAL(38, 0)`)

Example layout (using `{PREFIX}` = `GDP` as an example):
```sql
CREATE OR REPLACE ICEBERG TABLE DEV_GDP_SILVER_DB.OPPORTUNITY.OPPORTUNITY_CORE (

    -- -------------------------------------------------------------------------

    -- -------------------------------------------------------------------------
    OPPORTUNITY_CORE_SKEY       LONG                NOT NULL  COMMENT 'Surrogate key — auto-generated sequence',
    SOURCE_UNIQUE_ID            STRING              NOT NULL  COMMENT 'Unique identifier from the source system for traceability',
    REF_GDP_SOURCE_SYSTEM_SKEY  LONG                NOT NULL  COMMENT 'FK to REF_GDP_SOURCE_SYSTEM — identifies the source system',

    -- -------------------------------------------------------------------------
    -- ZONE 2: Business Columns  (no NOT NULL constraint)
    -- -------------------------------------------------------------------------
    REF_OPPORTUNITY_TYPE_SKEY   LONG                COMMENT 'FK to REF_OPPORTUNITY_TYPE',
    OPPORTUNITY_NAME            STRING              COMMENT 'Name of the opportunity',
    ...

    -- -------------------------------------------------------------------------
    -- ZONE 3: Hash Key  (NOT NULL)
    -- -------------------------------------------------------------------------
    OPPORTUNITY_CORE_HKEY       STRING              NOT NULL  COMMENT 'MD5/SHA2 hash of business attributes for change detection',

    -- -------------------------------------------------------------------------
    -- ZONE 4: Audit Columns  (NOT NULL)
    -- -------------------------------------------------------------------------
    GDP_IS_ACTIVE               BOOLEAN             NOT NULL  COMMENT 'Active flag for SCD2 — TRUE indicates current record',
    GDP_INSERTED_TS             TIMESTAMP_NTZ(6)    NOT NULL  COMMENT 'Timestamp when record was inserted',
    GDP_INSERTED_BY             STRING              NOT NULL  COMMENT 'User/process that inserted the record',
    GDP_UPDATED_TS              TIMESTAMP_NTZ(6)    NOT NULL  COMMENT 'Timestamp when record was last updated',
    GDP_UPDATED_BY              STRING              NOT NULL  COMMENT 'User/process that last updated the record',

    CONSTRAINT PK_OPPORTUNITY_CORE PRIMARY KEY (OPPORTUNITY_CORE_SKEY)
)
COMMENT='...'
EXTERNAL_VOLUME = 'GDP_SF_DEV_SILVER'
ICEBERG_VERSION = 2
CATALOG = 'SNOWFLAKE'
BASE_LOCATION = 'gdp-sf-dev-silver/opportunity/OPPORTUNITY_CORE/';
```

---

## Checklist Before Emitting DDL

- [ ] Sequence DDL emitted immediately before every table (both core and REF_ tables):
  ```sql
  CREATE OR REPLACE SEQUENCE {DB}.{SCHEMA}.SEQ_{TABLE}_SKEY
      START WITH 1
      INCREMENT BY 1
      NOORDER;
  ```
- [ ] Zone 1 columns present and in sub-order (own SKEY → SOURCE_UNIQUE_ID → REF_{PREFIX}_SOURCE_SYSTEM_SKEY → parent SKEYs)
- [ ] All Zone 1 columns have `NOT NULL` — no `DEFAULT` clause
- [ ] All columns are single-line: `COLUMN  TYPE  [COMMENT 'text'],`
- [ ] Zone 2 business columns present; `REF_*_SKEY` FKs listed first in Zone 2
- [ ] `IS_*` columns have `DEFAULT 'N'` — no NOT NULL
- [ ] Zone 3 HKEY present (auto-add if missing from ER) — always `NOT NULL`
- [ ] Zone 2 columns have **no** NOT NULL constraint (Zone 3 HKEY is `NOT NULL`)
- [ ] Zone 4 has all 5 audit columns, in fixed order, **all `NOT NULL`**
- [ ] `PK_` constraint references own SKEY
- [ ] Explicit `FK_` constraints emitted for all resolved parent/entity/reference SKEY columns
- [ ] Unresolved FK targets are flagged with `REVIEW`, not silently omitted or guessed
- [ ] Column name fixes applied: spaces → `_`; trailing `_F` → `_FLAG` (excludes `ACTIVE_F` and `{PREFIX}_*`)
- [ ] All columns have `COMMENT`
- [ ] Table has `COMMENT='...'`
- [ ] ICEBERG footer options present (`EXTERNAL_VOLUME`, `ICEBERG_VERSION`, `CATALOG`, `BASE_LOCATION`)
