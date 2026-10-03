# `_SKEY` Constraint Rules + `DIM_{PREFIX}_DATA_SOURCE_SKEY` Injection

`{PREFIX}` is the project's audit-column / shared-object namespace (e.g. `GDP`, `DW`, `EDW`; may be blank — gathered once per run, see SKILL.md Inputs).

## Rule 1 — Every `_SKEY` column MUST have a constraint

When cloning a table, scan every column ending in `_SKEY` and ensure it is covered by either a primary-key or foreign-key constraint.

### Classify each `_SKEY` column

| Pattern                     | Treatment                               |
|-----------------------------|------------------------------------------|
| `FACT_{X}_SKEY` on table `FACT_{X}` | Own PK |
| `DIM_{X}_SKEY` on table `DIM_{X}`   | Own PK |
| `DATE_{ROLE}_DIM_SKEY`              | FK to `DIM_DATE(DIM_DATE_SKEY)` |
| `{ENTITY}_DIM_SKEY`                 | FK to `DIM_{ENTITY}({DIM_{ENTITY}}_SKEY)` |
| `{ENTITY}_SKEY` (any other)         | FK to `DIM_{ENTITY}({DIM_{ENTITY}}_SKEY)` |
| `DIM_{PREFIX}_DATA_SOURCE_SKEY`     | FK to `{ENV}_{PREFIX}_SILVER_DB.SHARED.REF_{PREFIX}_SOURCE_SYSTEM({PREFIX}_SOURCE_SYSTEM_SKEY)` |

### Constraint syntax

```sql
-- Own PK
CONSTRAINT PK_{TABLE} PRIMARY KEY ({TABLE}_SKEY)

-- FK
CONSTRAINT FK_{TABLE}_{FK_COL}
  FOREIGN KEY ({FK_COL})
  REFERENCES {REF_DB}.{REF_SCHEMA}.{REF_TABLE}({REF_PK})
```

If the referenced DIM table cannot be resolved within the target schema, add `-- REVIEW: confirm reference target` next to the constraint instead of silently pointing elsewhere.

## Rule 2 — `DIM_{PREFIX}_DATA_SOURCE_SKEY` must exist on every table EXCEPT REF tables

> **EXCEPTION:** Tables whose name starts with `REF_` are reference/lookup tables and do NOT get `DIM_{PREFIX}_DATA_SOURCE_SKEY` injected. The source system FK belongs only on fact/core tables that ingest data from external sources, not on static reference tables.

For every **non-REF** table being cloned:

1. **Check** for column `DIM_{PREFIX}_DATA_SOURCE_SKEY` (case-insensitive).
2. If **missing**, inject it **immediately after the table's own `_SKEY` column** (i.e. after the PK, before any other FK SKEY).
3. Definition:

```sql
DIM_{PREFIX}_DATA_SOURCE_SKEY  NUMBER(3,0)  COMMENT 'FK to source system reference ({ENV}_{PREFIX}_SILVER_DB.SHARED.REF_{PREFIX}_SOURCE_SYSTEM)',  -- INJECTED BY schema-ddl-extraction
```

> **CRITICAL — type must be `NUMBER(3,0)`, not `LONG`.**
> The parent PK `{PREFIX}_SOURCE_SYSTEM_SKEY` on `REF_{PREFIX}_SOURCE_SYSTEM` is declared as `NUMBER(3,0)`.
> Iceberg requires exact PK/FK type match; declaring this column as `LONG` (NUMBER(19,0))
> raises `SQL compilation error: Primary key and foreign key data type does not match`.

4. Add the FK constraint inside the constraint block:

```sql
CONSTRAINT FK_{TABLE}_DIM_{PREFIX}_DATA_SOURCE_SKEY
  FOREIGN KEY (DIM_{PREFIX}_DATA_SOURCE_SKEY)
  REFERENCES {ENV}_{PREFIX}_SILVER_DB.SHARED.REF_{PREFIX}_SOURCE_SYSTEM({PREFIX}_SOURCE_SYSTEM_SKEY)  -- INJECTED BY schema-ddl-extraction
```

5. Use `NUMBER(3,0)` to match the parent PK exactly. Other FK SKEY columns may use `LONG` if their parent PK is `LONG`; this column is the exception because `REF_{PREFIX}_SOURCE_SYSTEM`'s PK is `NUMBER(3,0)`.

## Reporting

After applying both rules, emit a per-table summary:

```
TABLE: FACT_X
  - PK added            : PK_FACT_X (FACT_X_SKEY)
  - FK constraints added: 4
  - DIM_{PREFIX}_DATA_SOURCE_SKEY injected: YES
  - Unresolved FK refs  : 0
```
