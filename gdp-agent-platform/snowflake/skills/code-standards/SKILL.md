---
name: CODE-STANDARDS-REVIEW
type: VALIDATION
version: 1.0.0
description: How the code standards agent reviews a generated dbt project against the platform's written standards, area by area, before code review.
---

# Code standards review

The code standards agent reviews every generated dbt project independently of the agent that wrote it. It reads the
whole project and judges it against written standards only: this skill and the generation playbooks bound to the run
(GDP-DBT-ONBOARD-SOURCE, SILVER-MODEL or GOLD-MODEL, and the domain's own skill). It never edits code and never
reports a preference the standards do not state.

## How to judge

- Give every area a verdict: PASS (no violation), WARN (only deviations the standards discourage) or FAIL (at least
  one violation the standards require).
- Every finding cites the standard it breaks (skill name and rule), the file and a short excerpt.
- An **error** breaks a rule written as a requirement ("must", "always", "never", a rule numbered in a playbook).
  A **warning** is a deviation from guidance ("prefer", "should", "avoid").
- When the generated code follows a documented exception (an STTM anomaly, a domain contract override), it is not
  a finding.
- Do not invent files, tables or columns. Judge only what is in the project and the approved mapping.

## Areas

### NAMING: naming and layering
- Model files carry their layer prefix: `stg_` in staging, `int_` in intermediate, `dim_` or `fct_` in marts; Silver
  hubs and their HIST follow the playbook's naming.
- Column names are the approved target names from the mapping, upper or lower case as the playbook says, never a
  source name left in place.
- One model per file; file name equals model name.

### TYPES: casts and data types
- Every target column is cast explicitly to its target data type.
- Text columns are trimmed and empty strings turned into NULL (`NULLIF(TRIM(col), '')`) where the playbook requires it.
- Dates, timestamps and numbers use the playbook's safe conversion (`TRY_TO_*`) where source values can be invalid.

### KEYS: keys, joins and dedup
- The business key and the hash key are built from the columns the playbook lists, never from audit or surrogate
  key columns.
- Joins to reference data are LEFT joins unless the playbook says otherwise; join conditions use keys, not names.
- Deduplication keeps the latest record by the playbook's ordering; SCD1 and HIST handling follow the playbook.

### TESTS: tests and data quality
- Required columns carry `not_null`; business keys carry `unique` (or the combination test the playbook names).
- Columns with accepted values in the mapping carry `accepted_values`.
- The Soda checks file covers the same required columns and keys.

### DOCS: documentation and sources
- Every model and its columns are described in YAML.
- Sources are declared once in a sources file and read through `source()`; models read each other through `ref()`.
- No hardcoded database or schema names in model SQL.

### SQL: SQL style and safety
- No DDL or DML (`DROP`, `DELETE`, `TRUNCATE`, `ALTER`, `GRANT`, `INSERT`, `MERGE`) in models.
- No `SELECT *` in a final model; CTEs named for what they hold.
- Jinja delimiters balance and only use the project's macros.

## Reading the result

Each area is one validation check (`STANDARDS_NAMING`, `STANDARDS_TYPES`, ...). A FAIL area is a failed check, so it
lowers the validation score and, while `validation.standards_block` holds, its errors block code review. Improving
this skill (a new version in Skill Studio, then promoted) changes how the next review judges; improving a generation
playbook and regenerating changes the code it judges.
