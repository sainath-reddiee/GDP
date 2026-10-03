---
name: VALIDATION_SKILL
type: VALIDATION
version: 1.0.0
description: What runs before dbt code review and how to read the results.
---

# Validation

`CODEGEN.RUN_VALIDATION` runs, for the current generation:

- dbt parse and compile through a dbt project object (`EXECUTE DBT PROJECT ... WRITEBACK = FALSE`)
- SQL validation: the staging -> intermediate -> mart chain composed against the landed tables and explained
- data tests: not_null / unique / accepted_values evaluated on that composed query (equivalent of `dbt test`)
- naming, required columns, datatypes, STTM consistency and Soda configuration checks

Any FAILED or ERROR result blocks approval of the generated code.
