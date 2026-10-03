---
name: column-profiling
description: "Profile Snowflake table columns with SQL aggregations: row counts, null ratio, distinct counts, min/max, top-N values, and length ranges. Use when the user wants to: profile data, assess data quality, compute column statistics, understand distributions, or produce a profile JSON to feed descriptions/clustering/mapping."
parent_skill: ai-data-modeling
---

# Column Profiling

## When to Load
To characterize columns before description, clustering, modeling, or mapping. Works against
live tables (preferred) or previously generated sample CSVs.

## Technique

1. **One wide aggregation query per table** computing, per column:
   - `COUNT(*)` total rows and `COUNT({col})` non-null.
   - **Robust null detection**: treat trimmed placeholder strings as null (parameterize the
     placeholder set, e.g. empty, `N/A`, `NA`, `NULL`, `NONE`, `<NULL>`, `.`) in addition to
     physical NULLs.
   - `APPROX_COUNT_DISTINCT({col})` (HyperLogLog) for cardinality; exact `COUNT(DISTINCT)` only
     when precision is required.
   - `MIN`/`MAX` for numeric and date/time types.
   - `MIN(LENGTH(...))` / `MAX(LENGTH(...))` for string length range.
2. **Top-N frequency values** per column via a `GROUP BY {col} ORDER BY COUNT(*) DESC LIMIT N`.
3. **Sampling** for large tables with `TABLESAMPLE ({pct})` to bound cost.
4. **Unsupported-type handling**: skip string ops / distinct / grouping for semi-structured and
   spatial/binary types (parameterize the skip set, e.g. VARIANT, OBJECT, ARRAY, GEOGRAPHY,
   GEOMETRY, BINARY, VARBINARY); still record datatype and row counts.
5. Optionally enrich each column with deep analysis (see `ai-deep-quality-analysis`).

## Cortex/SQL Functions
`COUNT`, `COUNT DISTINCT`, `APPROX_COUNT_DISTINCT`, `MIN`, `MAX`, `LENGTH`, `TRIM`, `UPPER`,
`CAST(... AS STRING)`, `TABLESAMPLE`, `INFORMATION_SCHEMA`. No Cortex required.

## Parameters
- `tables` (list), `database`, `schema`.
- `null_placeholders` (set of strings to treat as null).
- `top_n` (frequency values), `sample_pct` (TABLESAMPLE), `unsupported_types` (skip set).

## Inputs / Outputs
- In: live connection + table list (or a directory of sample CSVs).
- Out: profile JSON `{ table: { col: { datatype, total, nulls, distinct, top_values,
  sample_values, range, length_range } } }` plus a simplified metadata JSON.

## Notes
- Batch column aggregates into a single query per table to minimize round-trips.
- CSV/offline profiling mirrors the same output shape (empty string = null, `Counter` for top-N).
