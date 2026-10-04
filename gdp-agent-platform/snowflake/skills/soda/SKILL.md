---
name: SODA_SKILL
type: SODA
version: 1.1.0
description: Extract client quality needs and write official SodaCL checks from the approved STTM.
---

# Soda checks

`CONTRACT.SODA_EXPECTATION_REGISTRY` is authoritative. The client brief (CSV, JSON, or notes)
is an input. Cortex extracts requirements. A person confirms each check. Confirmed and rejected
decisions are stored as `SODA_PATTERN` domain knowledge.

Write official SodaCL (Soda v3), not invented YAML.

```yaml
checks for dim_customer:
  - row_count > 0
  - missing_count(customer_id) = 0
  - duplicate_count(customer_id) = 0
  - invalid_count(customer_status) = 0:
      valid values: ['ACTIVE', 'INACTIVE']
  - invalid_count(email_address) = 0:
      valid format: email
  - freshness(loaded_at) < 1d
  - schema:
      fail:
        when required column missing: [customer_id, customer_name]
```

Use:

- `missing_count` for required columns; add `missing values` when NA / n/a should count as missing
- `duplicate_count` for business keys
- `invalid_count` with `valid values`, `valid regex`, `valid format`, `valid min` / `valid max`
- `freshness(column) < Nd` only — Soda does not allow `>` except on alert configs
- `schema` fail when required STTM columns are missing
- `values in (fk) must exist in other_table (pk)` for referential integrity
- Built-in formats: email, phone number, uuid, credit card number, date iso 8601, ipv4

Severity: FAIL for grain, keys, required columns, accepted codes. WARN for format, freshness, and soft distributions.
Do not invent columns that are not on the STTM. Do not publish to Soda Cloud from this factory.
