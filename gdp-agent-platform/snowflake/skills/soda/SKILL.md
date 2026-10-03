---
name: SODA_SKILL
type: SODA
version: 1.0.0
description: Generating Soda expectations from the approved STTM, domain rules and client requirements.
---

# Soda expectations

Expectations are generated from the approved STTM (required columns, business keys, accepted values, ranges),
domain Soda patterns and client requirements imported from Excel/CSV. The Snowflake registry
`CONTRACT.SODA_EXPECTATION_REGISTRY` is authoritative; Excel is only an input.

Severity: FAIL for keys and required columns, WARN for formats and distributions. The rendered SodaCL file is
`soda/checks/<model>.yml` and is executed by Soda Cloud in a later phase, not by this platform.
