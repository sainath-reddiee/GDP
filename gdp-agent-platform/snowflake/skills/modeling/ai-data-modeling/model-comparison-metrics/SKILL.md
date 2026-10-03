---
name: model-comparison-metrics
description: "Compare a generated data model/DDL against a reference and compute coverage and cohesion metrics. Use when the user wants to: compare two DDLs, measure schema coverage, find gaps/unmapped columns, get fuzzy match suggestions, or score model quality against a baseline."
parent_skill: ai-data-modeling
---

# Model Comparison & Metrics

## When to Load
After DDL generation, to quantify how well a generated model matches a reference model and to
surface gaps and near-matches.

## Technique

1. **Parse** both DDLs (regex `CREATE ... TABLE (...)`) into column sets; **normalize** datatypes
   via a parameterized map (e.g. collapse precision/length variants).
2. **Coverage**: exact set intersection of column names; report overlap %, unmapped columns.
3. **Fuzzy matching**: normalize keys (lowercase, strip underscores); rank near-matches with
   `SequenceMatcher` + token-set ratio above a parameterized threshold; project coverage boost if
   fuzzy matches were accepted.
4. **Token / structure analysis**: split names on `_`, count token reuse/uniqueness; extract
   hierarchical level numbers; classify columns as measure vs dimension by parameterized keyword
   heuristic.
5. **Semantic cohesion**: TF-IDF vectorize column names, KMeans cluster, compute `silhouette_score`
   as a cohesion proxy.
6. Cross-reference profile/description coverage to compute enrichment rates.

## Cortex/SQL Functions
None — offline analysis (scikit-learn, difflib).

## Parameters
- `datatype_normalization_map`, `fuzzy_threshold`, `measure_keywords`,
  `reference_ddl_path`, `generated_ddl_path`, `profile_path`, `description_path`.

## Inputs / Outputs
- In: reference DDL + generated DDL (+ optional profiles/descriptions/baseline metrics).
- Out: comparison JSON (overlap, gaps, fuzzy suggestions, type diffs) + metrics JSON
  (coverage %, token ratios, hierarchy depth, cohesion score, enrichment rates).
