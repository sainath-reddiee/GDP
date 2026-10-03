---
name: snowflake-stage-export
description: "Upload generated artifacts (CSV, JSON, DOCX) to a Snowflake stage and return a presigned download URL, with text/CSV sanitization. Use when the user wants to: export results to a stage, download artifacts, share a file from Streamlit-in-Snowflake, get a presigned URL, or persist pipeline outputs to Snowflake."
parent_skill: ai-data-modeling
---

# Snowflake Stage Export

## When to Load
When pipeline outputs must be saved to a Snowflake internal stage and handed back as a link,
especially from read-only environments (Streamlit-in-Snowflake).

## Technique

1. **Sanitize** text/CSV before upload: strip BOM and control characters, collapse CR/LF/TAB to
   spaces, truncate over-long values (parameterize `max_length`). Apply column-wise for DataFrames.
2. **Three-tier upload** (try in order):
   - Snowpark `put_stream` (preferred inside sessions),
   - presigned PUT URL via `GET_PRESIGNED_URL(stage, path, ttl, 'PUT')` then an HTTPS PUT
     (works in stored procs),
   - `PUT file://...` via a connector cursor (local dev).
3. Return a **presigned GET URL** via `GET_PRESIGNED_URL` for download (enforce a minimum TTL).

## Cortex/SQL Functions
`GET_PRESIGNED_URL`; Snowpark `put_stream`; connector `PUT`. No Cortex.

## Parameters
- `stage_name`, `subdirectory`, `expires_in` (TTL, with a minimum), `max_length` (sanitizer).

## Inputs / Outputs
- In: bytes + filename + stage name (+ optional connection/session).
- Out: `{ url, stage, stage_path, expires_at }`.

## Notes
- Grant the execution role PUT/GET on the stage. Organize uploads under a per-session subfolder.
