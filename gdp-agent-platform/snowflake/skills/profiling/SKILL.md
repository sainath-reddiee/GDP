---
name: PROFILING_SKILL
type: PROFILING
version: 1.0.0
description: Deterministic profiling of landed tables and how to read the profile.
---

# Profiling

`PROFILE.RUN_PROFILING` computes every statistic in SQL: row, null (including placeholder values such as N/A),
distinct, duplicate counts, min/max, length and numeric statistics, frequency distribution, character patterns,
date formats, enum candidates, potential keys and foreign keys. The LLM is used once per table, only to write
column descriptions and to name a semantic type when the deterministic rules cannot.

PII (email, phone, person name, date of birth) is detected from names, patterns and semantic types. Sample values
of PII columns are masked before they are stored and are never sent to the LLM.

When summarising a profile, quote statistics, not sample values.
