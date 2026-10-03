---
name: ai-model-generation
description: "Generate a harmonized dimensional data model (DIM/FACT entities with typed attributes) from column clusters using Snowflake Cortex AI_COMPLETE. Use when the user wants to: design a data model, propose Silver-layer entities, harmonize columns into tables, or auto-generate a logical schema from clustered columns."
parent_skill: ai-data-modeling
---

# AI Model Generation

## When to Load
After clustering, to have the LLM propose entities (dimensions/facts) and their attributes as a
structured JSON model, which `model-json-to-ddl` then renders to SQL.

## Technique

1. Assemble a **single-block prompt** (system + user combined — many chat models prefer one
   block) containing:
   - the **modeling rules** as parameters: naming convention (e.g. DIM_/FACT_ prefixes),
     1:1 column mapping expectation, datatype mapping, when to use semi-structured (VARIANT),
     qualified table names, transient vs permanent.
   - the **cluster JSON** as input context, truncated to a parameterized char budget to fit the
     context window.
2. Call `AI_COMPLETE('{llm_model}', '{prompt}')`.
3. Return the raw model JSON: `{ entities: [ { entity_name, purpose, attributes: [ { name,
   datatype, nullable, is_pk, source_columns, rationale } ] } ] }`.

## Cortex/SQL Functions
`AI_COMPLETE('{llm_model}', '{prompt}')`.

## Parameters
- `llm_model` (required), `naming_convention`, `datatype_map`, `target_schema`,
  `prompt_char_budget`.

## Inputs / Outputs
- In: cluster JSON from `semantic-column-clustering`.
- Out: model JSON (entities + attributes). No local fallback — surface errors clearly.

## Notes
- Keep modeling rules in the prompt as parameters so the same skill produces models for any
  domain/convention.
- The output is consumed by `model-json-to-ddl`; downstream parsing tolerates messy LLM JSON.
