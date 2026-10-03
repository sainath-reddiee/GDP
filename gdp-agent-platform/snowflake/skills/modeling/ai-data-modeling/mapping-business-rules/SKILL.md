---
name: mapping-business-rules
description: "Configurable rule engine that boosts, penalizes, or filters AI-generated mappings based on regex name patterns and score thresholds. Use when the user wants to: apply business rules to mappings, prefer/deprioritize certain tables or columns, auto-filter test/temp objects, or enforce mapping policy."
parent_skill: ai-data-modeling
---

# Mapping Business Rules

## When to Load
After mapping, to encode organizational preferences that adjust or filter mappings before review.

## Technique

1. Load rules from a JSON config (parameterize path; fall back to in-memory on read-only
   filesystems like Streamlit-in-Snowflake).
2. Each rule: a regex condition matched against `SourceTable`/`TargetTable`/`SourceColumn`/
   `TargetColumn` and/or a score threshold, plus an action and priority.
3. Apply enabled rules in **priority order**:
   - **BOOST** (+score), **PENALTY** (−score), **FILTER** (remove the mapping).
   - Append a note to the justification explaining each adjustment.
4. Return the adjusted/filtered mapping list. Rule patterns, thresholds, and boost/penalty
   magnitudes are all **parameters** — no domain patterns hardcoded.

## Cortex/SQL Functions
None — pure rule evaluation.

## Parameters
- `rules` (pattern, action, value, priority, enabled), `thresholds`, `rules_config_path`.

## Inputs / Outputs
- In: list of mapping dicts.
- Out: adjusted list with modified `MappingScore` and annotated `Justification`.
