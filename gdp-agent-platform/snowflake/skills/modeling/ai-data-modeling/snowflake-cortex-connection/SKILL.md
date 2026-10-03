---
name: snowflake-cortex-connection
description: "Establish a Snowflake connection that works both inside Streamlit-in-Snowflake (active session) and externally (connector via env vars), and smoke-test Cortex AI_COMPLETE / AI_EMBED availability. Use when the user wants to: connect to Snowflake, run in Streamlit-in-Snowflake, test Cortex is available, verify a model works, or set up shared connectivity for a pipeline."
parent_skill: ai-data-modeling
---

# Snowflake + Cortex Connection

## When to Load
First — every other sub-skill needs a connection. Provides one factory usable in SiS and locally.

## Technique

1. **Priority-ordered resolution**:
   - inside Snowflake (Streamlit-in-Snowflake / stored proc): use the active session
     (`get_active_session()`),
   - else explicit kwargs,
   - else environment variables.
2. **Account normalization**: strip a `.snowflakecomputing.com` suffix if present; some setups
   also normalize underscores/hyphens.
3. **Cortex smoke test**: run `SELECT AI_COMPLETE('{llm_model}', '...')` and
   `SELECT AI_EMBED('{embed_model}', '...')` to confirm entitlement and model availability in the
   region before running the pipeline.

## Cortex/SQL Functions
`AI_COMPLETE`, `AI_EMBED` (for the test); Snowpark `get_active_session` / `snowflake.connector`.

## Parameters
- Connection: `account`, `user`, auth (password or key-pair), `role`, `warehouse`, `database`,
  `schema` — via kwargs or env vars.
- `llm_model`, `embed_model` — for the smoke test (no default assumed available).

## Inputs / Outputs
- In: session/kwargs/env.
- Out: a live connection/session object; pass/fail Cortex diagnostics.

## Notes
- Keep model names as parameters — availability varies by region/account.
- Never print secrets; prefer key-pair or session auth over embedding passwords.
