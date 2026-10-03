---
name: GDP_DOMAIN_SKILL
type: DBT
domain: GDP
version: 1.0.0
description: GDP dbt standards - layering, naming, materializations, incremental and SCD patterns, approved macros, tests and documentation.
---

# GDP domain skill (dbt)

Use this skill when generating or reviewing dbt code for the GDP domain. The structured settings the generator
applies live in `config.json` next to this file; this text explains them for the agent and reviewers.

## Structure
- `models/staging/<source>/` - one `stg_<source>__<table>` view per landed table. Select from the source, rename columns
  to lower snake case, no business logic, no casts.
- `models/intermediate/` - `int_<entity>__deduplicated` (ephemeral). Applies the STTM deduplication rule.
- `models/marts/gdp/` - `dim_<entity>` / `fct_<process>`. Applies STTM transformations, casts to the STTM target type,
  derives surrogate keys and audit columns.

## Materializations
- Staging: view. Intermediate: ephemeral. Dimensions: incremental `merge` on the business key,
  `on_schema_change: fail`. Type 2 dimensions use snapshots (none in GDP Phase 1 models).

## Standards
- Surrogate keys: `MD5(CAST(<business key> AS VARCHAR))`.
- `RECORD_SOURCE` is the source system name; `LOADED_AT` is `CURRENT_TIMESTAMP()::TIMESTAMP_NTZ`.
- Every mart column is documented from the STTM business definition.
- Tests come from the STTM (not_null for required columns, unique for business keys, accepted_values) and
  data-quality checks from the approved Soda expectations.
- Use approved macros instead of re-writing the same expression: `initcap_trim`, `standardize_phone`,
  `mask_email`, `convert_timezone`, `clean_boolean`, `safe_divide`.

## Security
- No hard-coded credentials, no grants in models, PII columns are flagged in `meta.pii`.

## Exceptions
- Unparseable dates load as NULL (TRY_TO_DATE) and are reported by Soda as warnings.
