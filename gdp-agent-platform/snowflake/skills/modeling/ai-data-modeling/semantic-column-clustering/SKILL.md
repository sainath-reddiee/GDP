---
name: semantic-column-clustering
description: "Cluster columns by semantic similarity using Snowflake AI_EMBED embeddings and KMeans, with a TF-IDF fallback. Use when the user wants to: group related columns, find semantically similar fields, discover entity candidates, or reduce a wide column set into logical groupings before modeling."
parent_skill: ai-data-modeling
---

# Semantic Column Clustering

## When to Load
After descriptions exist, to group columns into logical clusters that inform entity design in
`ai-model-generation`.

## Technique

1. Build one text per column: `{table}.{column}: {description}`.
2. Embed all texts with `AI_EMBED('{embed_model}', '{text}')`, **batched** via `UNION ALL`
   (parameterize batch size) to bound query count.
3. Choose cluster count heuristically: `k = max(2, round(sqrt(n)))` (parameterize / allow override).
4. Cluster embedding vectors with KMeans (fixed `random_state` for reproducibility).
5. Emit cluster assignments keyed by `{table}.{column}`.

## Cortex/SQL Functions
`AI_EMBED('{embed_model}', '{text}')` (batched `UNION ALL`).

## Parameters
- `embed_model` (required), `batch_size`, `k` (or the sqrt heuristic), `random_state`.

## Inputs / Outputs
- In: `{ tables: { table: { col: "description" } } }`.
- Out: `{ embed_model, cluster_count, clusters: { id: ["table.col", ...] } }`.

## Fallbacks
- If `AI_EMBED` fails (or an explicit flag is set), fall back to local TF-IDF vectorization
  (parameterize `max_features`) and cluster those vectors instead.
