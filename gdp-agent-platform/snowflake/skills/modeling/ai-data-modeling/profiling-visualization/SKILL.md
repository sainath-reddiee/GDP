---
name: profiling-visualization
description: "Render profiling results as charts (null analysis, datatype distribution) and generate heuristic multi-persona data-quality insights — no database connection required. Use when the user wants to: visualize profiling, chart nulls/datatypes, get a data-quality summary, or produce persona-style narrative insights from a profile."
parent_skill: ai-data-modeling
---

# Profiling Visualization & Insights

## When to Load
After `column-profiling`, to present results visually and as readable findings. Offline — reads
the profile JSON, needs no connection.

## Technique

1. **Charts** (matplotlib):
   - null-analysis horizontal bar chart of top-N columns by null %,
   - datatype-distribution pie chart (simplify parameterized-precision types to base type).
2. **Heuristic persona insights**: from a profiling DataFrame, compute completeness/null metrics,
   flag low-completeness columns (below a threshold) and high-null columns (above a threshold),
   and format findings as multi-persona narratives with actionable rules. Thresholds and persona
   names/roles are **parameters** — no fixed domain wording.

## Cortex/SQL Functions
None — offline (matplotlib + pandas).

## Parameters
- `input_profile_path`, `output_charts_dir`, `top_n` (null chart),
  `completeness_threshold`, `null_threshold`, `personas` (name/role list).

## Inputs / Outputs
- In: profile JSON (from `column-profiling`) or a profiling DataFrame.
- Out: PNG charts; list of insight objects `{ agent, role, summary, highlights, rules }`.
