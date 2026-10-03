---
name: iterative-refinement
description: "Refine AI-generated mappings from user feedback by building an augmented prompt and tracking before/after improvement. Use when the user wants to: refine mappings, incorporate feedback, re-run mapping with corrections, improve low-confidence results, or iterate on a crosswalk."
parent_skill: ai-data-modeling
---

# Iterative Refinement

## When to Load
After a mapping pass and review, when the user provides feedback to improve results.

## Technique

1. **Assemble an augmented context/prompt** from structured sections:
   - free-text user feedback,
   - a capped set of approved mappings as **good examples**,
   - a capped set of rejected mappings as **bad examples**,
   - low-confidence mappings as **focus areas**.
2. Feed that enhanced business-context string back into `ai-schema-mapping` and re-run.
3. **Improvement stats**: diff refined vs original mappings — improved/degraded counts, average
   score delta, top improvements.
4. Record each refinement iteration (id, timestamp, feedback, stats) to a history store
   (parameterize path).

## Cortex/SQL Functions
None directly — produces the prompt/context consumed by `ai-schema-mapping`.

## Parameters
- `max_good_examples`, `max_bad_examples`, `max_focus_mappings`, `refinement_history_path`.

## Inputs / Outputs
- In: original mappings, user feedback, approved/rejected/low-confidence subsets.
- Out: enhanced context string; improvement-stats dict; appended iteration history.
