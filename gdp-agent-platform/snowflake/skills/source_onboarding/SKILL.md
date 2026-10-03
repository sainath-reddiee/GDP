---
name: SNOWFLAKE_SHARE_ONBOARDING
type: SOURCE_ONBOARDING
version: 1.0.0
description: How to onboard a Snowflake share or database - register, validate access, select objects, land as-is.
---

# Snowflake share and database onboarding

1. A share must be mounted by an admin first (`CREATE DATABASE <db> FROM SHARE <provider>.<share>` and
   `GRANT IMPORTED PRIVILEGES`). The platform never imports shares itself.
2. Register the source with `SOURCE.REGISTER_SOURCE` (type `SNOWFLAKE_SHARE` for mounted shares,
   `SNOWFLAKE_DATABASE` otherwise). Registration discovers tables and views.
3. The user selects objects; `SOURCE.VALIDATE_SOURCE_ACCESS` proves the platform can read each one.
   Failed checks return the run to source selection with a remediation message.
4. `SOURCE.EXECUTE_LANDING` copies each object as-is (CTAS) into `LANDING` and reconciles row counts.
   Shares are read-only and cannot be cloned or time-travelled, so CTAS is the supported copy.
5. Never put sample values from landed data into chat answers.
