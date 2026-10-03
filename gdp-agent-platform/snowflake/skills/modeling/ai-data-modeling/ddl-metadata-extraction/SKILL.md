---
name: ddl-metadata-extraction
description: "Extract Snowflake table DDL and build a structured metadata JSON of tables and columns. Use when the user wants to: pull CREATE TABLE statements, enumerate tables in a schema, parse DDL into a column catalog, or seed a modeling pipeline with schema metadata."
parent_skill: ai-data-modeling
---

# DDL & Metadata Extraction

## When to Load
First step when the source of truth is DDL rather than live data, or to build a
`{table -> columns[]}` catalog that downstream profiling/modeling steps consume.

## Technique

Two complementary paths:

1. **Live extraction from Snowflake**
   - Enumerate tables via `INFORMATION_SCHEMA.TABLES` filtered by `{database}` / `{schema}`.
   - For each table, fetch DDL with `SHOW CREATE TABLE {qualified_table}` (DDL text is in a
     known result column/row of the SHOW output).
   - Concatenate into a single `.sql` artifact with per-table comment separators.

2. **Offline parse of a DDL text file**
   - Regex-match `CREATE [OR REPLACE] [TRANSIENT] TABLE {qualified_table} (...)` blocks.
   - Split the column list, extract `name datatype` pairs, skip constraint/key lines.
   - Emit `{ "{qualified_table}": { "columns": [ { "name", "datatype" } ] } }`.

## Cortex/SQL Functions
`SHOW CREATE TABLE`, `INFORMATION_SCHEMA.TABLES`, `INFORMATION_SCHEMA.COLUMNS`. No Cortex.

## Parameters
- `database`, `schema` — scope of enumeration.
- `table_limit` — cap number of tables (optional).
- `input_ddl_path` / `output_metadata_path` — for the offline parse path.

## Inputs / Outputs
- In: live connection **or** a raw DDL text file.
- Out: concatenated DDL `.sql` and/or metadata JSON keyed by qualified table name.

## Notes
- Constraint lines (PRIMARY KEY, FOREIGN KEY, UNIQUE) must be skipped by the column parser.
- Keep table qualification (`db.schema.table`) intact so downstream steps stay unambiguous.
