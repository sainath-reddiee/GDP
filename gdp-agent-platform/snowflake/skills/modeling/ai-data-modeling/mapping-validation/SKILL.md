---
name: mapping-validation
description: "Validate AI-generated column mappings for type compatibility, SQL safety, and confidence thresholds. Use when the user wants to: validate mappings, check type compatibility, verify transformation SQL is safe, bucket mappings by confidence, or QA a source-to-target crosswalk."
parent_skill: ai-data-modeling
---

# Mapping Validation

## When to Load
After `ai-schema-mapping`, before approval/export — to flag risky or low-quality mappings.

## Technique

1. **Type compatibility**: classify source/target datatypes into groups (numeric, string,
   date/time, boolean). Same group = compatible; string→anything = allowed (castable);
   cross-group numeric/date = flag.
2. **SQL safety scan** of transformation logic: balanced parentheses and quotes; block
   dangerous keywords (parameterize the set, e.g. DROP, DELETE, TRUNCATE, ALTER).
3. **Per-mapping checks**: confidence thresholds (critical/warning/review bands as parameters),
   presence of transformation, source completeness, nullable constraints.
4. **Aggregate report**: counts of valid/invalid and confidence buckets
   (HIGH/MEDIUM/LOW by parameterized cutoffs), with per-mapping issues/warnings/recommendations.

## Cortex/SQL Functions
None — deterministic rule-based validation.

## Parameters
- `type_groups` (datatype → group), `dangerous_keywords`, `min_confidence`,
  confidence band cutoffs (`critical`, `warning`, `review`, `high`, `medium`).

## Inputs / Outputs
- In: list of mapping dicts (+ optional source/target profiles).
- Out: `{ total, valid_count, invalid_count, high/medium/low_confidence_count,
  validation_details: [ { is_valid, confidence_level, issues, warnings, recommendations } ], summary }`.

## Notes
- Treat explicitly UNMAPPED columns as valid-with-warning rather than failures.
