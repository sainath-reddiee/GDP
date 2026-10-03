---
name: model-json-to-ddl
description: "Convert a logical data-model JSON into executable Snowflake CREATE TABLE DDL, with robust parsing of messy LLM output. Use when the user wants to: generate DDL from a model, emit CREATE TABLE statements, materialize a schema design, or turn an entities JSON into SQL."
parent_skill: ai-data-modeling
---

# Model JSON → Snowflake DDL

## When to Load
After `ai-model-generation`, to render the entities JSON into deployable SQL.

## Technique

1. **Robust parse** of the model JSON (LLM output is often messy). Try in order:
   - direct JSON parse,
   - strip markdown code fences then parse,
   - extract from first `{` to last `}`,
   - clean up string-concatenation artifacts (`"..." + "..."`), backslash continuations, and
     double-wrapped/stringified JSON.
2. For each entity: sanitize identifiers, **normalize datatypes** via a parameterized map
   (generic type → Snowflake type), deduplicate columns.
3. **Primary-key detection**: honor an explicit `is_pk` attribute flag, else match names against
   a parameterized PK-candidate set.
4. Emit `CREATE [TRANSIENT] TABLE IF NOT EXISTS {db}.{schema}.{table} (...)` and optional
   `COMMENT ON COLUMN` statements.

## Cortex/SQL Functions
None — pure template generation (produces DDL, does not execute it).

## Parameters
- `database`, `schema`, `transient` (bool), `datatype_map`, `pk_candidates`,
  `emit_comments` (bool), `input_model_path`, `output_sql_path`.

## Inputs / Outputs
- In: model JSON (entities + attributes).
- Out: `.sql` file of CREATE TABLE (+ COMMENT) statements.

## Stopping Points
- Present the DDL for review; do not execute it without explicit user approval.
