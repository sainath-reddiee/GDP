# Schema Configuration — Data Readiness Check (Optional)

This file is an **optional, user-maintained** configuration. The skill does not ship any pre-filled schema list or exception notes — fill in the sections below for your own project, or skip this file entirely and let the skill ask for schemas at runtime (see SKILL.md Step 0).

## Schemas to Check

Fill in the databases/schemas this project wants checked:

| # | Database | Schema | Description |
|---|----------|--------|-------------|
| 1 | `<DATABASE_1>` | `<SCHEMA_1>` | `<what this schema contains>` |
| 2 | `<DATABASE_2>` | `<SCHEMA_2>` | `<what this schema contains>` |

## Table Exclusion Rules

Exclude tables matching ANY of these patterns (defaults — edit as needed):
- `TABLE_NAME LIKE '%_HIST'` — historical snapshot tables
- `TABLE_NAME LIKE '%_TEMP'` — temporary tables
- `TABLE_NAME LIKE '%_BKP'` — backup tables
- `TABLE_NAME LIKE 'REF_%'` — reference/lookup tables
- `TABLE_NAME LIKE '%XREF%'` — cross-reference tables
- `TABLE_NAME = 'flyway_schema_history'` — migration tracking

## Versioning Notes (Status Overrides) — optional

### Not Tracking (exclusions)
| Schema | Table | Reason |
|--------|-------|--------|
| `<SCHEMA>` | `<TABLE>` | `<why this table is excluded from checks>` |

### Going to Drop
| Schema | Table | Reason |
|--------|-------|--------|
| `<SCHEMA>` | `<TABLE>` | `<why this table is scheduled for removal, and any SKEY references that should not be flagged as issues>` |

## SKEY → Target Dimension Mapping — optional

Use this table to help the skill identify which dimension a SKEY column references, when it isn't obvious from the column name:

| SKEY Column | Target Dimension Table | Target Schema |
|-------------|----------------------|---------------|
| `<DIM_X_SKEY>` | `<DIM_X>` | `<DATABASE.SCHEMA>` |
