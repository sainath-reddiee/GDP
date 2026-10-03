---
name: MAPPING_SKILL
type: MAPPING
version: 1.0.0
description: Hybrid source-to-target mapping - features, weights, recommendations and what reviewers need.
---

# Hybrid mapping

`MAPPING.GENERATE_MAPPING_CANDIDATES` scores every source column against every target column with seven
components, each between 0 and 1:

| Component | Evidence |
|---|---|
| semantic | cosine similarity of AI_EMBED (snowflake-arctic-embed-l-v2.0) of column descriptions and business definitions |
| keyword | name tokens after abbreviation expansion (cust -> customer, nm -> name, dob -> birth date) |
| datatype | type family, length and precision compatibility, castability |
| statistical | null rate vs nullability, uniqueness vs business key, semantic type and value fit |
| domain | glossary synonyms and business rules in the domain knowledge |
| context | source table entity vs target table entity |
| historical | previously approved mappings and seeded mapping patterns |

Weights and thresholds come from `MAPPING.MAPPING_SCORING_CONFIG` (never hard-coded). Embedding similarity is one
component, never the decision. The LLM only explains ambiguous cases; it does not change the ranking.

Reviewers approve, reject, modify the transformation or choose a different target. Anything that needs business
interpretation requires a business justification. No mapping is authoritative until approved.
