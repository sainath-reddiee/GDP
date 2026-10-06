# Great Expectations (GX Core 1.x), high level

GX validates data against an **Expectation Suite**, a named list of expectations. The runtime objects:

- **Data Context**: the project (file-based or ephemeral) that holds configuration.
- **Data Source** and **Data Asset**: a connection (for example Snowflake) and a table or query in it.
- **Batch Definition**: which slice of the asset to validate (whole table, or by a date column).
- **Expectation Suite**: the rules. Each expectation has a `type`, `kwargs` and optional `meta`.
- **Validation Definition**: a suite bound to a batch definition.
- **Checkpoint**: runs validation definitions and triggers **Actions** (update Data Docs, notify Slack/email).
- **Data Docs**: generated HTML report of results.

Key options on most column expectations:

- `mostly` (0 to 1): the share of rows that must pass; `mostly: 0.95` tolerates 5% violations.
- `result_format`: BOOLEAN_ONLY, BASIC, SUMMARY or COMPLETE, controlling how many failing values are returned.
- `meta`: free-form notes; this factory stores severity, origin, requirement and evidence there.

## Mapping from this factory's checks

| Check kind | SodaCL | GX expectation |
|---|---|---|
| row_count | `row_count > 0` / `between a and b` | `expect_table_row_count_to_be_between` |
| schema | `schema: fail: when required column missing` | `expect_table_columns_to_match_set` (exact_match false) |
| not_null | `missing_count(c) = 0` | `expect_column_values_to_not_be_null` |
| missing_percent | `missing_percent(c) < p%` | `expect_column_values_to_not_be_null` with `mostly = 1 - p/100` |
| unique (one column) | `duplicate_count(c) = 0` | `expect_column_values_to_be_unique` |
| unique (compound) | `duplicate_count(a, b) = 0` | `expect_compound_columns_to_be_unique` |
| accepted_values | `invalid_count(c) = 0` + `valid values` | `expect_column_values_to_be_in_set` |
| regex | `invalid_percent(c)` + `valid regex` | `expect_column_values_to_match_regex` (with `mostly`) |
| format | `valid format: email` | `expect_column_values_to_match_regex` with the format's regex |
| range | `valid min` / `valid max` | `expect_column_values_to_be_between` |
| max_length | `valid max length: n` | `expect_column_value_lengths_to_be_between` (max_value n) |
| freshness | `freshness(c) < 1d` | `unexpected_rows_expectation` (MAX(c) older than the threshold) |
| reference | `values in (fk) must exist in t (pk)` | `unexpected_rows_expectation` (NOT EXISTS against t) |
| failed rows | `failed rows` + `fail query` | `unexpected_rows_expectation` with the same query |

Other useful expectations: `expect_column_values_to_be_in_type_list`, `expect_column_min_to_be_between`,
`expect_column_max_to_be_between`, `expect_column_mean_to_be_between`,
`expect_column_proportion_of_unique_values_to_be_between`, `expect_column_pair_values_a_to_be_greater_than_b`,
`expect_multicolumn_sum_to_equal`, `expect_column_distinct_values_to_be_in_set`.

## When to choose which

- Soda: YAML checks close to SQL, quick to review, alert levels (warn/fail) built in, good fit for pipelines
  that already run Soda Library or Soda Cloud.
- GX: Python-first teams, rich Data Docs reporting, and checkpoints with actions. Severity is not a native
  pass/fail level in older versions, so this factory records it in `meta.severity`.

The generated suite is a portable artifact. To run it: create a Data Source for the Snowflake account, add the
target table as a Data Asset, load the suite JSON, bind it in a Validation Definition and run a Checkpoint.
