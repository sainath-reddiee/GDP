---
name: mapping-pattern-library
description: "Store, retrieve, and recommend reusable column-mapping patterns across projects, including industry-template patterns. Use when the user wants to: reuse mapping patterns, get mapping recommendations, apply common transformations, bootstrap mappings from templates, or share learned patterns across projects."
parent_skill: ai-data-modeling
---

# Mapping Pattern Library

## When to Load
To reuse proven mappings across runs/projects and to seed recommendations from templates.

## Technique

1. **Learn a pattern** from a high-scoring mapping (above a `min_score`): derive a regex from the
   column name (e.g. suffix-based), record source/target/table patterns and a transformation
   template with placeholders. De-duplicate against existing patterns, incrementing
   confidence/occurrences (cap confidence).
2. **Industry templates**: keep a set of prebuilt patterns as **parameters** (name pattern +
   transformation template). Do not hardcode a specific industry vocabulary in the skill.
3. **Recommend**: regex-match a target column against learned patterns and templates, return the
   top-N by confidence with the transformation to apply.

## Cortex/SQL Functions
None — heuristic/regex.

## Parameters
- `pattern_store_path`, `industry_templates`, `min_score_to_learn`, `initial_confidence`,
  `confidence_increment`, `recommendation_limit`.

## Inputs / Outputs
- In: a mapping (to learn) or a target column + candidate sources (to recommend).
- Out: pattern id; recommendations `[ { target_column, source_column, transformation,
  confidence, pattern_id, reason, tags } ]`; persisted `{ patterns, industry_templates, metadata }`.
