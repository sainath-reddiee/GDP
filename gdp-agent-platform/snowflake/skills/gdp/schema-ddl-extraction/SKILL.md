---
name: schema-ddl-extraction
description: >
  Clone all objects (tables, sequences, views, materialized views, dynamic tables, procedures,
  functions, streams, tasks, file formats, stages) from a source Snowflake schema to a target
  schema, aligning every DDL with Gold/Silver standards. Enforces _SKEY constraints
  (PK/FK), injects DIM_{PREFIX}_DATA_SOURCE_SKEY when missing (FK to
  {ENV}_{PREFIX}_SILVER_DB.SHARED.REF_{PREFIX}_SOURCE_SYSTEM({PREFIX}_SOURCE_SYSTEM_SKEY)), and emits a
  validated per-object runbook script.
  Trigger phrases: "copy schema", "recreate schema", "clone schema", "duplicate schema",
  "move objects from X to Y", "schema migration with layer standards", "rebuild schema".
---

# Schema Clone — Cross-Schema Object Migration Skill

## When to Use

Load this skill whenever the user asks to:
- Copy / recreate / clone all objects from one schema to another schema (same or different DB)
- Migrate a schema while applying Gold or Silver layer standards
- Refresh a *_DEV / *_TEST schema from a baseline schema
- Generate a "per-object" runbook DDL script to rebuild a schema cleanly

This skill **delegates** the per-table DDL formatting rules to:
- [`gold-model`](../gold-model/SKILL.md) for Gold layer tables
- [`silver-model`](../silver-model/SKILL.md) for Silver layer tables

This skill **adds** the orchestration, inventory, and constraint-enforcement rules.

---

## Inputs

Ask the user for the following if not already provided:
1. **Source schema** — fully qualified, e.g. `DEV_{PREFIX}_GOLD_COMPANY_DB.COMPANY`
2. **Target schema** — fully qualified, e.g. `DEV_{PREFIX}_GOLD_COMPANY_DB.COMPANY_DEV`
3. **Layer** — Gold or Silver (drives which sister skill applies). Auto-detect from DB name when possible (`*_GOLD_*` → Gold, `*_SILVER_*` → Silver).
4. **Environment** — DEV / QA / UAT / PROD (default DEV).
5. **Project prefix (`{PREFIX}`)** — the short namespace used for audit columns and shared reference objects on this project (e.g. `GDP`, `DW`, `EDW`). Ask once; reuse for the whole run. May be blank.
6. **Replace mode** — `CREATE OR REPLACE` (default) vs `CREATE IF NOT EXISTS`.
7. **Sequence START strategy (MANDATORY question)** — Before emitting any DDL, ALWAYS ask the user:
   > "How should sequence `START` values be set for **all tables (fact/core AND REF)**?
   > (a) Derive from `MAX(<TABLE>_SKEY) + 1` of a source schema *(user specifies the source schema)*
   > (b) Default `START 1`
   > (c) Custom values per-table"

   Do NOT proceed past Step 1 until this is answered. If (a) is chosen, probe `MAX(<SKEY>)` per table from the named source (REF tables included) and use `MAX+1` as `START`. Add an inline comment showing the source MAX value for traceability.

---

## Procedure

Follow these steps **in order**:

### Step 1 — Inventory the source schema

Run a single query against `INFORMATION_SCHEMA` (or `SHOW`) to enumerate every object:

```sql
SELECT TABLE_NAME, TABLE_TYPE
FROM <SOURCE_DB>.INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = '<SOURCE_SCHEMA>';

SHOW SEQUENCES IN SCHEMA <SOURCE_DB>.<SOURCE_SCHEMA>;
SHOW PROCEDURES IN SCHEMA <SOURCE_DB>.<SOURCE_SCHEMA>;
SHOW USER FUNCTIONS IN SCHEMA <SOURCE_DB>.<SOURCE_SCHEMA>;
SHOW VIEWS IN SCHEMA <SOURCE_DB>.<SOURCE_SCHEMA>;
SHOW MATERIALIZED VIEWS IN SCHEMA <SOURCE_DB>.<SOURCE_SCHEMA>;
SHOW DYNAMIC TABLES IN SCHEMA <SOURCE_DB>.<SOURCE_SCHEMA>;
SHOW STREAMS IN SCHEMA <SOURCE_DB>.<SOURCE_SCHEMA>;
SHOW TASKS IN SCHEMA <SOURCE_DB>.<SOURCE_SCHEMA>;
SHOW FILE FORMATS IN SCHEMA <SOURCE_DB>.<SOURCE_SCHEMA>;
SHOW STAGES IN SCHEMA <SOURCE_DB>.<SOURCE_SCHEMA>;
```

Record an inventory list and detect each object type. See [./references/object-extraction.md](./references/object-extraction.md).

### Step 1.5 — Sequence START strategy (MANDATORY user prompt)

**STOP — DO NOT PROCEED** to Step 2 until the user has answered the sequence START question (see Inputs #7). This is mandatory for every schema clone.

Present this prompt verbatim to the user:

> **Sequence START values — choose one:**
> - **(a) MAX-derived** — probe `MAX(<TABLE>_SKEY) + 1` from a source/reference schema (you specify which). Recommended when cloning over an existing dataset to avoid PK collisions.
> - **(b) Default `START 1`** — fresh schema, no existing IDs to preserve.
> - **(c) Custom** — provide explicit START values per table.
>
> *Applies to BOTH fact/core tables AND REF tables.*

If user picks **(a)**:
1. Ask for the source schema FQN.
2. Build a single batch probe query:
   ```sql
   SELECT '<TABLE>' AS T, COALESCE(MAX(<SKEY>), 0) AS M
   FROM <SOURCE_DB>.<SOURCE_SCHEMA>.<TABLE>
   UNION ALL ...
   ```
3. Compute `START = M + 1` for each table; if the table is empty/missing, fall back to `START 1` and tag with `-- source MAX = 0 (empty)`.
4. Emit each `CREATE OR REPLACE SEQUENCE` with the derived START and an inline `-- source MAX = N` comment for traceability.

If user picks **(b)**: emit all sequences with `START 1`.

If user picks **(c)**: collect per-table values from the user; flag any unanswered table with `-- REVIEW`.

### Step 2 — Extract DDL per object

Use `GET_DDL` per object type (preferring per-object granularity over schema-level dumps so each object can be aligned independently):

```sql
SELECT GET_DDL('TABLE',     '<SOURCE_DB>.<SOURCE_SCHEMA>.<NAME>', TRUE);
SELECT GET_DDL('SEQUENCE',  '<SOURCE_DB>.<SOURCE_SCHEMA>.<NAME>');
SELECT GET_DDL('VIEW',      '<SOURCE_DB>.<SOURCE_SCHEMA>.<NAME>');
SELECT GET_DDL('PROCEDURE', '<SOURCE_DB>.<SOURCE_SCHEMA>.<NAME>(<ARG_TYPES>)');
SELECT GET_DDL('FUNCTION',  '<SOURCE_DB>.<SOURCE_SCHEMA>.<NAME>(<ARG_TYPES>)');
-- ... etc
```

**CRITICAL — Never trust truncated GET_DDL output.** If the result is truncated (look for `... [N lines truncated] ...` or `(Some row/cell values were truncated...)`) you MUST recover the missing column metadata by querying `INFORMATION_SCHEMA.COLUMNS` directly:

```sql
SELECT
  COLUMN_NAME,
  DATA_TYPE,
  NUMERIC_PRECISION,
  NUMERIC_SCALE,
  CHARACTER_MAXIMUM_LENGTH,
  IS_NULLABLE,
  COLUMN_DEFAULT,
  COMMENT,
  ORDINAL_POSITION
FROM <SOURCE_DB>.INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = '<SOURCE_SCHEMA>' AND TABLE_NAME = '<TABLE>'
ORDER BY ORDINAL_POSITION;
```

Use this metadata to reconstruct EVERY column declaration with the EXACT type, precision, scale, default, and comment from the source. **NEVER guess** precision/scale, **NEVER** substitute `NUMBER` for `DECIMAL` (or vice-versa) — preserve source style verbatim. **NEVER** drop a column. **NEVER** paraphrase a comment.

For tables, also describe columns and constraints:
```sql
DESC TABLE <SOURCE_DB>.<SOURCE_SCHEMA>.<NAME>;
SHOW PRIMARY KEYS   IN TABLE <SOURCE_DB>.<SOURCE_SCHEMA>.<NAME>;
SHOW IMPORTED KEYS  IN TABLE <SOURCE_DB>.<SOURCE_SCHEMA>.<NAME>;
SHOW UNIQUE KEYS    IN TABLE <SOURCE_DB>.<SOURCE_SCHEMA>.<NAME>;
```

### Step 3 — Align each table DDL with layer standards

For **each table**, apply the rules from the appropriate sister skill:
- Gold layer → [`gold-model`](../gold-model/SKILL.md)
- Silver layer → [`silver-model`](../silver-model/SKILL.md)

See [./references/alignment-rules.md](./references/alignment-rules.md) for the exact items to enforce: data types, audit columns, HKEY position, COMMENTs, ICEBERG footer, naming fixes, etc.

### Step 4 — Enforce `_SKEY` constraints (mandatory)

For every `*_SKEY` column that lacks a constraint, add one. See [./references/skey-constraint-rules.md](./references/skey-constraint-rules.md).

Rules:
- **Own PK SKEY** (e.g. `FACT_X_SKEY` on `FACT_X`, `DIM_X_SKEY` on `DIM_X`):
  `CONSTRAINT PK_{TABLE} PRIMARY KEY ({TABLE}_SKEY)`
- **FK SKEY** (any other `*_SKEY` column):
  `CONSTRAINT FK_{TABLE}_{FK_COL} FOREIGN KEY ({FK_COL}) REFERENCES {REF_DIM_TABLE}({REF_DIM_TABLE}_SKEY)`
  - Resolve `{REF_DIM_TABLE}` by stripping the trailing `_SKEY` and prefixing `DIM_` if not already present.
  - For role-playing date FKs (`DATE_*_DIM_SKEY`) → reference `DIM_DATE(DIM_DATE_SKEY)`.
  - Flag unresolved references with `-- REVIEW`.

### Step 5 — Inject `DIM_{PREFIX}_DATA_SOURCE_SKEY` if missing (mandatory)

For every table:
1. Check whether `DIM_{PREFIX}_DATA_SOURCE_SKEY` is already a column.
2. If **absent**, inject it **immediately after the table's own `*_SKEY` column** with the following definition:

```sql
DIM_{PREFIX}_DATA_SOURCE_SKEY  NUMBER(3,0)  COMMENT 'FK to source system reference ({ENV}_{PREFIX}_SILVER_DB.SHARED.REF_{PREFIX}_SOURCE_SYSTEM)',
```

> **Type note:** Use `NUMBER(3,0)` (NOT `LONG`) to match the parent PK
> `{ENV}_{PREFIX}_SILVER_DB.SHARED.REF_{PREFIX}_SOURCE_SYSTEM.{PREFIX}_SOURCE_SYSTEM_SKEY` which is `NUMBER(3,0)`.
> Iceberg tables enforce exact PK/FK type match — a `LONG` (NUMBER(19,0)) FK column will fail
> with: `SQL compilation error: Primary key and foreign key data type does not match`.

3. Add a FK constraint:
```sql
CONSTRAINT FK_{TABLE}_DIM_{PREFIX}_DATA_SOURCE_SKEY
  FOREIGN KEY (DIM_{PREFIX}_DATA_SOURCE_SKEY)
  REFERENCES {ENV}_{PREFIX}_SILVER_DB.SHARED.REF_{PREFIX}_SOURCE_SYSTEM({PREFIX}_SOURCE_SYSTEM_SKEY)
```

4. Tag the injected column with `-- INJECTED BY schema-ddl-extraction` so reviewers can see what changed.

### Step 6 — Rewrite object references to target schema

In every extracted DDL (tables, views, MVs, procedures, functions, streams, tasks):
- Replace `<SOURCE_DB>.<SOURCE_SCHEMA>.` → `<TARGET_DB>.<TARGET_SCHEMA>.`
- Preserve cross-schema references that intentionally point outside the source schema. Flag with `-- REVIEW` when ambiguous.
- For sequences attached to `*_SKEY` columns, re-emit `CREATE OR REPLACE SEQUENCE {TARGET}.SEQ_{TABLE}_SKEY START 1 INCREMENT 1` (preserve START/INCREMENT from source if non-default).

### Step 7 — Assemble final runbook

Use [./assets/clone_runbook_template.sql](./assets/clone_runbook_template.sql). Order:
1. `CREATE SCHEMA IF NOT EXISTS <TARGET>`
2. Sequences
3. Tables (with all PK / UK / FK constraints inline)
4. Views
5. Materialized Views
6. Dynamic Tables
7. Streams
8. File Formats / Stages
9. Functions
10. Procedures
11. Tasks (resumed last)
12. Grants (if requested)

Each object lives in its own labeled section:
```
-- ============================================================================
-- TABLE: FACT_OPP_PROJECT
-- ============================================================================
```

### Step 8 — Validate

For each generated SQL block, run `snowflake_sql_execute` with `only_compile=true` to confirm it parses. Fix any compile errors before delivery.

### Step 8.5 — Generate optional DATA RELOAD section (MANDATORY user prompt)

After DDL is finalised, ALWAYS ask the user:

> **Data reload — do you want to populate the cloned target schema with data from a source schema?**
> - **(yes)** specify the source schema FQN (often the same one used for sequence MAX probing)
> - **(no)** ship DDL only

If **yes**:
1. Append a clearly-labelled `-- DATA RELOAD` section to the runbook **after** the DDL section.
2. For every table in the inventory, emit:
   ```sql
   INSERT INTO <TARGET_SCHEMA>.<TABLE> SELECT * FROM <SOURCE_SCHEMA>.<TABLE>;
   ```
3. **Order the INSERTs by FK dependency** — REF / lookup tables first, then dimension tables, then fact / core, then `*_HIST`. Use the FK graph from `SHOW IMPORTED KEYS` to derive the topological order; flag cycles with `-- REVIEW`.
4. Verify column count parity (`INFORMATION_SCHEMA.COLUMNS`) between source and target before emitting `SELECT *`. If counts differ, emit an explicit column list and tag with `-- REVIEW: column drift detected`.
5. Append a **row-count validation query** at the end of the section that compares source vs target counts for every table.
6. Optionally precede each INSERT with `TRUNCATE TABLE <TARGET_SCHEMA>.<TABLE>;` if the user wants the script to be idempotently re-runnable without re-running DDL.

If **no**: end the runbook at the DDL section.

### Step 9 — Deliver

Output:
1. Inventory summary table (object_type, count, names).
2. Final runbook SQL (single file, sectioned per object).
3. A "changes applied" report listing per-table:
   - Columns whose data type was normalised
   - `_SKEY` constraints added
   - Audit columns added/moved
   - Naming fixes (`-- REVIEW` flags)

---

## Key Rules (Quick Reference)

| Rule | Detail |
|---|---|
| Always emit per-object DDL | One labeled section per object, not a single dump |
| `*_SKEY` must have a constraint | Own PK or FK; never untagged |
| `REF_{PREFIX}_SOURCE_SYSTEM_SKEY` is mandatory on every table | Inject directly after own `_SKEY` if missing; FK to `{ENV}_{PREFIX}_SILVER_DB.SHARED.REF_{PREFIX}_SOURCE_SYSTEM({PREFIX}_SOURCE_SYSTEM_SKEY)` |
| Delegate per-table formatting | To `gold-model` or `silver-model` |
| Order matters | Sequences → Tables → Views → MVs → Dynamic Tables → Streams → File Formats → Stages → Functions → Procedures → Tasks |
| Replace fully qualified references | Source schema → target schema in every DDL |
| Validate before delivery | `only_compile=true` on every block |
| Default replace mode | `CREATE OR REPLACE` unless user says otherwise |
| **Sequence START strategy** | **MANDATORY user prompt** — must ask (a) MAX-derived / (b) START 1 / (c) custom before emitting DDL. Applies to ALL tables incl. REF. |
| **Exact replication** | When cloning, the target DDL MUST replicate the source EXACTLY. Never guess type/precision/scale; always recover from `INFORMATION_SCHEMA` if `GET_DDL` is truncated. Never substitute `NUMBER` for `DECIMAL`. Never paraphrase comments. Never drop columns. |
| **Data reload** | **MANDATORY user prompt** after DDL is finalised — must ask whether to append a `-- DATA RELOAD` section that `INSERT INTO target SELECT * FROM source` for every table, ordered by FK dependency, with row-count validation at the end. |
| Flag unknowns with `-- REVIEW` | Never silently drop or guess |

---

## Reference Files

- [Object extraction patterns](./references/object-extraction.md)
- [Layer alignment rules (delegation)](./references/alignment-rules.md)
- [_SKEY constraint enforcement + DIM_{PREFIX}_DATA_SOURCE_SKEY injection](./references/skey-constraint-rules.md)
- [Runbook template](./assets/clone_runbook_template.sql)
