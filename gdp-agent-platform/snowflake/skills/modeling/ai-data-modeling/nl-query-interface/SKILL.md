---
name: nl-query-interface
description: "Conversational natural-language interface over mappings using Snowflake Cortex — classify intent, extract entities, and answer questions with mapping context. Use when the user wants to: ask about mappings in plain English, chat with the mapping results, search/update mappings via natural language, or get an NL assistant over a crosswalk."
parent_skill: ai-data-modeling
---

# Natural-Language Query Interface

## When to Load
To let users interrogate and act on mappings conversationally instead of via forms.

## Technique

Two LLM-backed modes plus a fallback:

1. **Interpret query** — send a structured few-shot prompt to
   `SNOWFLAKE.CORTEX.COMPLETE('{llm_model}', '{prompt}')`; parse a JSON response for
   `{ intent, entities, confidence, clarification_needed, clarification_questions,
   suggested_action }`. Intents: map / search / update / validate / help.
2. **Answer with context** — pass a capped slice of the current mappings to the LLM and let it
   answer the question directly (markdown).
3. **Keyword fallback** — when no connection is available, classify intent by keyword matching.
4. `execute_intent` dispatches the interpreted intent to the corresponding action.

## Cortex/SQL Functions
`SNOWFLAKE.CORTEX.COMPLETE('{llm_model}', '{prompt}')`.

## Parameters
- `llm_model` (required — do not hardcode a specific model), `max_context_mappings`.

## Inputs / Outputs
- In: user query + context `{ current_mappings, available_tables, recent_actions }`.
- Out: intent object, or a markdown answer, or an action result. Conversation history kept in memory.
