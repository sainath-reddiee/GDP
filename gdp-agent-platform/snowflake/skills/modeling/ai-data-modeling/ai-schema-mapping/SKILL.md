---
name: ai-schema-mapping
description: "Map source columns to target-schema columns using Snowflake Cortex — either a single-shot AI_COMPLETE pass or a scalable iterative/batched approach with AI_EMBED pre-filtering and cumulative state merging. Use when the user wants to: map source to target, build a crosswalk, match columns across schemas, generate transformation logic, or do source-to-target column mapping at scale."
parent_skill: ai-data-modeling
---

# AI Source-to-Target Schema Mapping

## When to Load
When mapping profiled source columns onto an existing target schema, with confidence scores,
rationale, and transformation SQL per mapping.

## Technique

Choose based on scale:

### Mode A — Single-shot
- Build one prompt listing all source columns, all target columns, and business context (each
  column summarized: type, distinct count, top sample values, description).
- Call `AI_COMPLETE('{llm_model}', '{prompt}')` once; request a JSON array of
  `{source, target, confidence, rationale, transformation}`; parse (handle markdown/double-stringify).
- Best for small schemas.

### Mode B — Iterative / batched (scalable)
1. **Descriptions**: generate short semantic descriptions per column batch (see `ai-column-descriptions`).
2. **Embedding pre-filter**: embed source & target descriptions with `AI_EMBED('{embed_model}',
   '{text}')` (batched), compute a cosine-similarity matrix, keep top-K source candidates per target.
3. **Compatibility scoring**: rank candidates by weighted signals — semantic-type match, embedding
   similarity, exact/contains/word-overlap name similarity, type compatibility, quality score
   (weights are parameters).
4. **Iterative mapping**: process targets in batches of `target_batch_size`, scan source in
   chunks of `source_chunk_size`, call `AI_COMPLETE` per (target-batch × source-chunk) with a
   structured output schema + few-shot examples (direct, format conversion, standardization,
   composite, type cast).
5. **State merge**: accumulate results across chunks so later chunks never erase earlier
   mappings; prefer higher-confidence entries.
6. Optionally apply learned patterns (see `mapping-learning-engine`).

## Cortex/SQL Functions
`AI_COMPLETE('{llm_model}', '{prompt}')`, `AI_EMBED('{embed_model}', '{text}')`.

## Parameters
- `llm_model`, `embed_model` (both required).
- `target_batch_size`, `source_chunk_size`, `top_k` (candidates), scoring `weights`,
  `business_context`, `semantic_hints`.

## Inputs / Outputs
- In: source & target profiles (+ optional descriptions, similarity hints, business context).
- Out: list of mapping dicts: `{ TargetTable, TargetColumn, TargetDataType, SourceTable,
  SourceColumn, SourceDataType, MappingScore, Justification, TransformationLogic, WorkNotes }`.

## Fallbacks
- Description gen → title-cased column name. Embedding step → skip similarity (non-fatal).
- If pre-filter leaves too few candidates, fall back to the full source chunk.
- Robust multi-strategy JSON parsing on every LLM response.
