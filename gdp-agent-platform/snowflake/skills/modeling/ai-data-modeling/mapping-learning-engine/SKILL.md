---
name: mapping-learning-engine
description: "Learn reusable patterns from user corrections to mappings and apply score boosts to future mappings. Use when the user wants to: learn from corrections, remember mapping preferences, improve future mappings automatically, or capture feedback as reusable rules."
parent_skill: ai-data-modeling
---

# Mapping Learning Engine

## When to Load
To make the mapper improve over time by capturing how users correct its output.

## Technique

1. **Record corrections**: for each user fix, store the original mapping, the corrected
   source/table/transformation, and feedback (assign corrected mappings a high confidence).
2. **Extract patterns** via frequency analysis (`Counter`) over the correction history:
   - recurring target column-name suffix → preferred source-name pattern,
   - recurring target-table → preferred source-table pattern,
   - only emit patterns meeting a `min_occurrences` threshold.
3. **Apply patterns**: regex-match current mappings against learned patterns and add a bounded
   score boost (cap the boost per pattern type).

## Cortex/SQL Functions
None — statistical/heuristic.

## Parameters
- `correction_history_path`, `min_occurrences`, `max_column_boost`, `max_table_boost`,
  `corrected_confidence`.

## Inputs / Outputs
- In: original mapping + corrected values + feedback (for recording); mapping list (for applying).
- Out: correction ids; learned patterns `[ { pattern_type, target_pattern,
  preferred_source_pattern, confidence_boost, occurrences, reason } ]`; boosted mappings.
