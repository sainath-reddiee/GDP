---
name: ai-data-modeling
description: "AI-powered Snowflake data modeling pipeline using Cortex (AI_COMPLETE, AI_EMBED). Transforms raw/source (Bronze) tables into a harmonized modeled (Silver) layer, and maps source columns to an existing target schema. Use this skill whenever the user mentions: bronze to silver, data modeling, harmonize schema, source-to-target mapping, column profiling, semantic clustering, generate a data model or DDL from tables, AI column descriptions, mapping validation, or any Cortex-driven schema design task — even if they don't name a specific step."
---

# AI Data Modeling (Bronze → Silver + Source-to-Target Mapping)

A pipeline of reusable techniques for AI-assisted Snowflake data modeling with Cortex.
This file is a **thin router**: pick the mode, then load the sub-skill(s) for the steps needed.
Every sub-skill is generic — supply project values (databases, schemas, tables, model names,
domain hints) as parameters. No values are hardcoded.

## Two Modes

- **Generate New Model** — profile source tables, describe + cluster columns, let AI design
  entities, emit DDL. Path: `column-profiling` → `ai-column-descriptions` →
  `semantic-column-clustering` → `ai-model-generation` → `model-json-to-ddl`.
- **Map to Existing Target** — profile both source and target, generate descriptions, run AI
  mapping, then validate/govern. Path: `column-profiling` (both sides) →
  `ai-column-descriptions` → `ai-schema-mapping` → `mapping-validation` → feedback-loop skills.

## Pipeline

```
                         source tables
                              |
        +---------------------+---------------------+
        |                     |                     |
 ddl-metadata-        column-profiling        (synthetic-data-
  extraction               |                    generation, optional)
                           |
              +------------+------------------------------+
              |            |                              |
   ai-deep-quality   ai-column-descriptions        ai-schema-mapping
     -analysis            |                              |
                   semantic-column-clustering       mapping-validation
                          |                              |
                   ai-model-generation            business-rules / approval /
                          |                        refinement / learning /
                   model-json-to-ddl               pattern-library / nl-query
                          |
                   model-comparison-metrics

  cross-cutting: snowflake-cortex-connection, snowflake-stage-export,
                 ai-code-sandbox-validation, profiling-visualization
```

## Intent Table (route to sub-skill)

| User intent / trigger | Load sub-skill |
|-----------------------|----------------|
| Connect to Snowflake, test Cortex availability, dual-mode session | `snowflake-cortex-connection/SKILL.md` |
| Extract DDL / build metadata JSON from tables | `ddl-metadata-extraction/SKILL.md` |
| Profile columns (nulls, distinct, min/max, top values) | `column-profiling/SKILL.md` |
| Create fake/sample data for offline profiling | `synthetic-data-generation/SKILL.md` |
| Deep AI column analysis, quality score, remediation SQL | `ai-deep-quality-analysis/SKILL.md` |
| Business-friendly column descriptions via LLM | `ai-column-descriptions/SKILL.md` |
| Group columns by semantic similarity | `semantic-column-clustering/SKILL.md` |
| Design entities (DIM/FACT) with AI | `ai-model-generation/SKILL.md` |
| Turn a model JSON into CREATE TABLE DDL | `model-json-to-ddl/SKILL.md` |
| Map source columns to target columns with AI | `ai-schema-mapping/SKILL.md` |
| Validate mappings (types, SQL safety, confidence) | `mapping-validation/SKILL.md` |
| Compare generated vs reference model, coverage metrics | `model-comparison-metrics/SKILL.md` |
| Apply configurable boost/penalty/filter rules to mappings | `mapping-business-rules/SKILL.md` |
| Approve/reject mappings, track review stats | `mapping-approval-workflow/SKILL.md` |
| Refine mappings from user feedback | `iterative-refinement/SKILL.md` |
| Learn patterns from user corrections | `mapping-learning-engine/SKILL.md` |
| Store/recommend reusable mapping patterns | `mapping-pattern-library/SKILL.md` |
| Ask questions about mappings in natural language | `nl-query-interface/SKILL.md` |
| Export artifacts to a stage, presigned URL | `snowflake-stage-export/SKILL.md` |
| Safely execute AI-generated Python | `ai-code-sandbox-validation/SKILL.md` |
| Chart profiling results, persona insights | `profiling-visualization/SKILL.md` |

## Orchestration

For a full unattended run, execute the "Generate New Model" or "Map to Existing Target" path
in order, passing each step's output as the next step's input. All steps share one connection
(see `snowflake-cortex-connection`). Steps are independently runnable, so the user can start
mid-pipeline if intermediate artifacts already exist.

## Stopping Points
- After profiling / mapping, present results and pause before AI generation or DDL execution.
- Before executing any generated DDL or AI-generated Python, get explicit user approval.
- Warn before large scans or many Cortex calls (credit cost).

## Conventions for all sub-skills
- Never hardcode databases, schemas, tables, model names, file paths, stage names, or domain
  vocabulary — accept them as parameters.
- Cortex model names (LLM and embedding) are always parameters with no default assumed available.
- Support both Streamlit-in-Snowflake (active session) and external connector execution.
