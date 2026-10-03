---
name: ai-column-descriptions
description: "Generate business-friendly column descriptions using Snowflake Cortex AI_COMPLETE, with a deterministic template fallback. Use when the user wants to: describe columns, document a schema, enrich a data catalog, or produce semantic text to feed clustering and mapping."
parent_skill: ai-data-modeling
---

# AI Column Descriptions

## When to Load
After profiling, to turn column stats into short semantic descriptions that clustering,
modeling, and mapping steps can embed and reason over.

## Technique

Two modes:

1. **LLM mode** — build a prompt per column from `{table, column, datatype, profile stats,
   top/sample values, semantic hint}` and call `AI_COMPLETE('{llm_model}', '{prompt}')`.
   - Instruct: single paragraph, business-friendly, length-bounded (e.g. < N chars).
   - **Semantic hints** are parameterized name→meaning rules (e.g. suffix `_DT` → timestamp,
     `COUNTRY` → country code). Never hardcode domain-specific hints; pass them in.
2. **Template mode** (fallback) — deterministically compose a description from stats, top
   values, range, and the semantic hint string — no LLM.

Batch columns to reduce Cortex calls where possible.

## Cortex/SQL Functions
`AI_COMPLETE('{llm_model}', '{prompt}')`.

## Parameters
- `llm_model` (required), `semantic_hints` (name-pattern → hint), `max_chars`, `mode` (llm|template).

## Inputs / Outputs
- In: metadata + profile JSON.
- Out: `{ mode, llm_model, tables: { table: { col: "description" } }, failures: [] }`.

## Fallbacks
- If the connection/model is unavailable, fall back from `llm` to `template` mode.
- If an individual `AI_COMPLETE` call fails, use the template description and record the error.
