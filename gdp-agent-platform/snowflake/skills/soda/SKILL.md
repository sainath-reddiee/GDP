---
name: SODA_SKILL
type: SODA
version: 2.0.0
description: Design data quality checks from the dataset profile, the approved STTM and client rules; write official SodaCL first and a Great Expectations suite second.
---

# Data quality checks (Soda first, Great Expectations second)

`CONTRACT.SODA_EXPECTATION_REGISTRY` is authoritative. Every check is stored once in a neutral form
(`check_type` plus a `definition` with a `kind`) and rendered to SodaCL (primary) and to a GX Core 1.x
expectation suite (secondary). A person confirms each check; confirmed and rejected decisions become
`SODA_PATTERN` knowledge and steer the next run.

## Where checks come from (in this order of authority)

1. **Client rules**: a brief, CSV or JSON from the client. Origin `CLIENT`, or `AI` when Cortex extracted it.
2. **The STTM**: grain keys, required columns, accepted values, ranges, formats. Origin `STTM` / `DOMAIN_RULE`.
3. **The dataset profile**: what the source data shows today. Origin `PROFILE`. See "Designing from the dataset".
4. **Learned patterns**: accepted transformation checks (`TRANSFORM`) and earlier reviewer decisions.

A rule that a higher source already states is not repeated; the profile adds evidence to it instead.

## Designing from the dataset

Read the staged profile of each source column that feeds a target column (via the STTM line), then:

| Profile shows | Check | Severity |
|---|---|---|
| 0% nulls | `missing_count(col) = 0` | WARN (FAIL only if the STTM says required) |
| 0 < nulls <= 20% | `missing_percent(col)` warn above 1.5x observed + 1 point | WARN |
| <= 20 distinct codes, low cardinality, not PII | `invalid_count(col)` with `valid values` | WARN (FAIL when the STTM lists them) |
| one shape covers >= 95% of text values | `invalid_percent(col)` with `valid regex`, warn above 5% | WARN |
| numeric minimum >= 0 | `invalid_count(col)` with `valid min: 0` | WARN |
| longest source value vs target `VARCHAR(n)` | `invalid_count(col)` with `valid max length: n` | FAIL (truncation) |
| driving table row count N | `row_count` warn when not between 0.9N and 1.1N | WARN; widen for filtering models |
| spoke of a hub (domain contract) | `values in (fk) must exist in hub (fk)` | FAIL |

Evidence only flows through value-preserving mappings. DIRECT lines carry every rule. Lines that only wrap
the column (TRIM, CAST, NULLIF, TO_DATE...) carry completeness, length and numeric bounds. Derived
expressions (COALESCE, CASE, concatenation, lookups) carry nothing; check them on the built model.

Always attach the evidence ("3% nulls in 12,400 rows of CRM.CUSTOMER.SEGMENT") so the reviewer sees why a
threshold was chosen. When the profile contradicts the STTM (values outside the accepted list, source longer
than the target type), say so in the evidence: that is a mapping defect, not a quality threshold.

## Backtest before acceptance

Run the proposed checks on today's source data (one aggregate query per table, caller's role). A check that
fails on current data is either a real defect to raise with the source owner or a threshold to relax.
Never accept a FAIL check that fails today without a reviewer note.

## Severity policy

- FAIL: grain and business keys (unique, not null), STTM-required columns, accepted codes from the STTM or
  client, referential integrity to the hub, schema (required columns), truncation risk.
- WARN: everything inferred from the profile, formats, freshness, volume bands and soft distributions.
- Do not mark a check FAIL only because the profile happened to be clean.

## SodaCL v3 essentials

Thresholds go on the check line for a single FAIL condition. Use an alert configuration for WARN, or for
both levels. Never combine a threshold on the check line with `warn:`.

```yaml
checks for dim_customer:
  # volume
  - row_count > 0
  - row_count:
      warn: when not between 900 and 1100
  # completeness
  - missing_count(customer_id) = 0
  - missing_percent(segment):
      warn: when > 6%
      fail: when > 20%
  - missing_count(phone):
      missing values: ['N/A', 'n/a', '']
      warn: when > 0
  # uniqueness
  - duplicate_count(customer_id) = 0
  - duplicate_count(source_system, source_id) = 0
  # validity
  - invalid_count(status) = 0:
      valid values: ['ACTIVE', 'INACTIVE']
  - invalid_percent(postal_code):
      valid regex: '^[0-9]{5}$'
      warn: when > 5%
  - invalid_count(email) = 0:
      valid format: email
  - invalid_count(credit_limit) = 0:
      valid min: 0
  - invalid_count(customer_name) = 0:
      valid max length: 50
  # numeric metrics
  - min(credit_limit) >= 0
  - avg(order_amount) between 10 and 500
  # freshness
  - freshness(updated_at) < 1d
  - freshness(loaded_at):
      warn: when > 1d
      fail: when > 3d
  # schema
  - schema:
      fail:
        when required column missing: [customer_id, customer_name]
        when wrong column type:
          customer_id: varchar
  # referential integrity
  - values in (company_core_skey) must exist in company_core (company_core_skey)
  # failed rows with a condition or a query
  - failed rows:
      name: Close date after create date
      fail condition: close_date < created_date
  - failed rows:
      name: Active row per key
      fail query: |
        SELECT source_unique_id FROM company_core WHERE gdp_is_active
        GROUP BY source_unique_id HAVING COUNT(*) > 1
  # user-defined metric
  - pct_closed_won between 5 and 60:
      pct_closed_won query: |
        SELECT 100 * COUNT_IF(stage = 'Closed Won') / COUNT(*) FROM opportunity_core
```

Other building blocks:

- Validity keys: `valid values`, `valid format`, `valid regex`, `valid min` / `valid max`,
  `valid length` / `valid min length` / `valid max length`. Built-in formats include email, phone number,
  uuid, ip address / ipv4, credit card number, date iso 8601, date us, date eu, decimal, integer, percentage.
- `missing values` / `invalid values` extend what counts as missing or invalid.
- Check-level `filter: country = 'US'`, or dataset filters `checks for orders [daily]` with
  `filter orders [daily]: where: order_date > CURRENT_DATE - 1`.
- `name:` gives a readable title; `attributes:` tags checks for routing (owner, dimension, priority).
- `for each dataset T:` applies the same checks to many tables.
- Cross-dataset: `row_count same as other_table` compares volumes.
- Anomaly detection (`anomaly detection for row_count`) and change-over-time (`change for row_count < 50`)
  need Soda Cloud history; propose them only when the client runs Soda Cloud.

## Great Expectations (secondary output)

The same checks are rendered as a GX Core 1.x expectation suite (JSON). See
`references/great-expectations.md` for the mapping and runtime concepts. Rules without a native
expectation (freshness, cross-table references, failed-row queries) use `unexpected_rows_expectation`
with a query over `{batch}`.

## Do not

- Invent columns or tables that are not on the STTM or the target model.
- Copy PII values into `valid values`; profile values of PII columns are masked.
- Propose uniqueness on non-identifier columns because a small sample happened to be unique.
- Publish to Soda Cloud from this factory.
