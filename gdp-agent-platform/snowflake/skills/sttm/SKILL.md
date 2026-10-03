---
name: STTM_SKILL
type: STTM
version: 1.0.0
description: The source-to-target mapping contract - table design and line rules.
---

# STTM

The STTM is generated only from approved mapping decisions plus target metadata. It has two parts:

- Table design: grain, business keys, deduplication rule, SCD type, incremental strategy, source tables.
- Lines: one per target column with mapping type (DIRECT, TRANSFORM, DERIVED, CONSTANT, UNMAPPED), transformation,
  business rule and definition, nullable and uniqueness rules, accepted values, range, SCD behaviour, default,
  filter, join and lookup logic, exception handling, confidence and approval.

Required target columns cannot be UNMAPPED. Soda expectations and dbt code are projections of the approved STTM.
