---
name: silver-model
description: >
  Silver layer Snowflake Iceberg DDL generator. Use when creating, scaffolding, or generating
  a new Silver model or table from an ER diagram, entity list, or column spec. Applies correct
  column zone ordering (keys → business → hash key → audit), data types, NOT NULL constraints,
  foreign key constraints, naming conventions (SKEY, HKEY, REF_, {PREFIX}_, SOURCE_), and Snowflake ICEBERG DDL structure
  automatically — regardless of the order columns appear in the ER diagram. Trigger phrases:
  "create silver model", "scaffold silver table", "generate DDL", "new iceberg table",
  "silver layer", "silver model from ER", "create model", "new silver entity".
---

# Silver Model — DDL Generator Skill

## When to Use

Load this skill whenever the user asks to:
- Create or scaffold a new Silver-layer Snowflake Iceberg table
- Generate a DDL from an ER diagram, entity description, or column list
- Add a new table to the Silver layer (any domain: company, property, opportunity, employee, contact, etc.)
- Review or fix the column ordering / data types of an existing Silver DDL

---

## Procedure

Follow these steps **in order** every time:

### Step 1 — Gather inputs

Ask the user for the following if not already provided:
1. **Entity name** — e.g. `OPPORTUNITY_CORE` (determines table name, SKEY name, HKEY name, sequence name)
2. **Schema / domain** — e.g. `OPPORTUNITY` (if not obvious from the entity name)
3. **ER diagram or column list** — paste as text, image, or describe the columns
4. **Parent entity** (if this is a child/detail table) — e.g. "child of OPPORTUNITY_CORE"
5. **Environment** — default to `DEV` unless stated otherwise
6. **Project prefix (`{PREFIX}`)** — the short namespace used for audit columns and shared reference objects on this project (e.g. `GDP`, `DW`, `EDW`). Ask once per project; reuse for every table. If the user has no such convention, leave blank — audit columns then have no leading prefix (e.g. `INSERTED_TS` instead of `{PREFIX}_INSERTED_TS`).

### Step 2 — Classify all columns into zones

Using the rules in [./references/column-classification.md](./references/column-classification.md):

- **Ignore the order columns appear in the ER diagram entirely.**
- Apply the classification decision tree to EVERY column from the ER.
- Assign each column to Zone 1, 2, 3, or 4.
- If a column is ambiguous, place it in Zone 2 and add `-- REVIEW: zone placement inferred`.
- **Always inject Zone 3 (HKEY) even if not in the ER.**
- **Always inject Zone 4 (all 5 audit columns) even if not in the ER.**

### Step 3 — Assign data types and nullability

Using [./references/data-types.md](./references/data-types.md):

- Zone 1 → always `NOT NULL`; SKEYs → `LONG`; `SOURCE_UNIQUE_ID` → `STRING`
- Zone 2 → `NULL` by default; `IS_*` flags → `STRING DEFAULT 'N'`; REF FK → `LONG`; business text → `STRING`; dates → `TIMESTAMP_NTZ(6)`; geo/scores → `FLOAT`
- Zone 3 → `STRING NOT NULL` — HKEY is always NOT NULL
- Zone 4 → fixed types, all `NOT NULL` (see data-types.md Zone 4 table)

### Step 4 — Apply DDL conventions

Using [./references/snowflake-ddl-conventions.md](./references/snowflake-ddl-conventions.md):

- Build the full DDL by filling in [./assets/iceberg_ddl_template.sql](./assets/iceberg_ddl_template.sql)
- Replace all `{PLACEHOLDER}` tokens with actual values (including `{PREFIX}` from Step 1)
- Add a `COMMENT` to every column
- Add the table-level `COMMENT`
- Add the `CONSTRAINT PK_{TABLE_NAME}` line
- Add explicit foreign key constraints for all resolved parent/entity/reference SKEY columns
- If an FK target cannot be resolved confidently from the ER, repo, or naming convention, emit a `-- REVIEW: confirm FK target` note
- Add the ICEBERG footer (`EXTERNAL_VOLUME`, `ICEBERG_VERSION`, `CATALOG`, `BASE_LOCATION`)

### Step 5 — Emit and explain

Output the complete DDL, then provide a brief summary table:

| Zone | Columns included |
|---|---|
| Zone 1 — Keys | list column names |
| Zone 2 — Business | list column names |
| Zone 3 — Hash key | `{ENTITY}_HKEY` |
| Zone 4 — Audit | `{PREFIX}_IS_ACTIVE`, `{PREFIX}_INSERTED_TS`, `{PREFIX}_INSERTED_BY`, `{PREFIX}_UPDATED_TS`, `{PREFIX}_UPDATED_BY` |

Flag any columns placed in Zone 2 as `-- REVIEW` so the user can verify them.

---

## Key Rules (Quick Reference)

| Rule | Detail |
|---|---|
| Column order is always by zone, not by ER diagram | Zone 1 → Zone 2 → Zone 3 → Zone 4 |
| Zone 1 sub-order is fixed | Own SKEY → SOURCE_UNIQUE_ID → REF_{PREFIX}_SOURCE_SYSTEM_SKEY → parent SKEYs |
| Zone 4 is always injected | All 5 audit columns always present, even if missing from ER |
| HKEY is always injected | Add `{ENTITY}_HKEY` Zone 3 even if missing from ER |
| IS_* flags always get DEFAULT 'N' | Never nullable boolean; stored as STRING |
| HKEY is always NOT NULL | `{ENTITY}_HKEY STRING NOT NULL` |
| Column name fixes | Spaces → `_`; trailing `_F` → `_FLAG` (excludes `ACTIVE_F` and `{PREFIX}_*`) |
| All _SKEY columns use LONG | Not DECIMAL(38,0) |
| All Zone 1 and Zone 4 are NOT NULL | No exceptions |
| Emit FK constraints for resolved relationships | Parent SKEYs and `REF_*_SKEY` columns should become `FK_` constraints when target tables are known |
| IDs stored as STRING | Even numeric-looking source IDs (e.g. DUNS_NUMBER) |
| REF_ FKs in Zone 2 first | Before other domain attributes, after Zone 1 |
| Every column needs COMMENT | No column without a COMMENT clause |
| DEV is the default environment | Unless user specifies QA/UAT/PROD |

---

## Column Naming Conventions

| Pattern | Meaning | Zone |
|---|---|---|
| `{ENTITY}_SKEY` | Surrogate key (sequence-generated) | Zone 1 (own) or Zone 1.4 (parent FK) |
| `{ENTITY}_HKEY` | SHA2/MD5 hash of business columns | Zone 3 |
| `REF_{TABLE}_SKEY` | FK to reference/lookup table | Zone 2 |
| `{PREFIX}_*` | Audit columns (project-configurable prefix) | Zone 4 |
| `SOURCE_*` | Raw unmodified source values | Zone 2 (end of Zone 2 block) |
| `IS_*` | Boolean flag (stored as STRING) | Zone 2 |
| `SEQ_{TABLE}_SKEY` | Snowflake sequence name for SKEY | Referenced in DEFAULT clause |
| `PK_{TABLE}` | Primary key constraint name | Constraint line |
| `FK_{TABLE}_{TARGET}` | Foreign key constraint name | Constraint line |

---

## Reference Files

- [Column classification rules and decision tree](./references/column-classification.md)
- [Data types and nullability per zone](./references/data-types.md)
- [Snowflake DDL conventions, ICEBERG options, formatting rules](./references/snowflake-ddl-conventions.md)
- [Annotated DDL template with zone markers](./assets/iceberg_ddl_template.sql)
