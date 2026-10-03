---
name: synthetic-data-generation
description: "Generate realistic synthetic CSV sample data from table/column metadata for offline profiling and testing. Use when the user wants to: create fake/sample/mock data, generate test rows without touching production, or profile a pipeline without live data access."
parent_skill: ai-data-modeling
---

# Synthetic Data Generation

## When to Load
When live data is unavailable or sensitive, but the modeling/profiling pipeline still needs
representative rows per table.

## Technique

1. **Column-name → domain inference**: match the column name against parameterized vocabulary
   categories (e.g. dates/months, currencies, countries, business units, identifiers) and draw
   values from the matching vocabulary. Keep vocabularies as **parameters**, not hardcoded
   domain lists.
2. **Datatype fallback** when no name pattern matches:
   - string/VARCHAR → random tokens,
   - numeric/NUMBER/FLOAT → random ints/decimals within a parameterized range,
   - date/timestamp → random dates within a parameterized window,
   - boolean → random true/false.
3. Emit one CSV per table with `row_count` rows; column order follows metadata.

## Cortex/SQL Functions
None — pure offline generation.

## Parameters
- `metadata` (table→columns), `output_dir`, `row_count`.
- `vocabularies` (name-pattern → value list), `numeric_ranges`, `date_window`.

## Inputs / Outputs
- In: metadata JSON of tables/columns.
- Out: one CSV file per table.

## Notes
- Output feeds `column-profiling` (CSV path). Keep the CSV schema identical to real tables so
  the same profiler works unchanged.
