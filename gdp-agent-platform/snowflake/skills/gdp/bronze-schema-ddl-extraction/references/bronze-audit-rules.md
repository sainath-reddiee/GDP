# Bronze — Audit Field Rules

`{PREFIX}` is the project's audit-column namespace (e.g. `GDP`, `DW`, `EDW`; may be blank — established once per project, see SKILL.md).

## Required Audit Fields (mandatory on every Bronze table)

All 5 fields must appear as the **last columns** in the table, in this exact order:

| # | Column | Type | Nullable | Purpose |
|---|---|---|---|---|
| 1 | `{PREFIX}_INSERTED_TS` | `TIMESTAMP_NTZ(6)` | `NOT NULL` | When the record was first loaded into Bronze |
| 2 | `{PREFIX}_IS_ACTIVE` | `BOOLEAN` | `NOT NULL` | Soft delete flag — `TRUE` = active, `FALSE` = logically deleted |
| 3 | `{PREFIX}_ROW_HASH` | `STRING` | `NOT NULL` | MD5/SHA2 hash of all business (non-audit) columns for change detection |
| 4 | `{PREFIX}_UPDATED_TS` | `TIMESTAMP_NTZ(6)` | `NOT NULL` | When the record was last updated in Bronze |
| 5 | `ETL_CREATED_TS` | `TIMESTAMP_NTZ(6)` | `NOT NULL` | When the ETL process created this record in the source staging area |

---

## Rules

1. **All 5 fields are mandatory** — no Bronze table may omit any of them.
2. **Recommended NOT NULL** — ideally all 5 fields should be NOT NULL, but when generating deployment DDL, **preserve the actual nullability from the source table**. Report nullability gaps in the compliance report only — never silently alter them in the DDL output.
3. **Order is fixed** — `{PREFIX}_INSERTED_TS` → `{PREFIX}_IS_ACTIVE` → `{PREFIX}_ROW_HASH` → `{PREFIX}_UPDATED_TS` → `ETL_CREATED_TS`.
4. **No other columns after audit fields** — audit fields are always the last columns before any constraint declarations.
5. **`{PREFIX}_ROW_HASH` replaces HKEY** — in Bronze, the hash column is named `{PREFIX}_ROW_HASH` (not `*_HKEY` as in Silver/Gold).
6. **`ETL_CREATED_TS` is Bronze-specific** — this field does NOT exist in Silver or Gold layers; it captures the source ETL pipeline timestamp.

---

## Validation Checklist

When reviewing a Bronze table for compliance:

- [ ] `{PREFIX}_INSERTED_TS` exists and is `TIMESTAMP_NTZ(6) NOT NULL`
- [ ] `{PREFIX}_IS_ACTIVE` exists and is `BOOLEAN NOT NULL`
- [ ] `{PREFIX}_ROW_HASH` exists and is `STRING NOT NULL`
- [ ] `{PREFIX}_UPDATED_TS` exists and is `TIMESTAMP_NTZ(6) NOT NULL`
- [ ] `ETL_CREATED_TS` exists and is `TIMESTAMP_NTZ(6) NOT NULL`
- [ ] Audit fields are the last 5 columns (before constraints)
- [ ] No business columns appear after audit fields

---

## Common Violations

| Violation | Fix |
|---|---|
| Missing `{PREFIX}_ROW_HASH` | Add `{PREFIX}_ROW_HASH STRING NOT NULL COMMENT 'Hash of business columns for change detection'` |
| Missing `ETL_CREATED_TS` | Add `ETL_CREATED_TS TIMESTAMP_NTZ(6) NOT NULL COMMENT 'ETL pipeline creation timestamp'` |
| `{PREFIX}_IS_ACTIVE` as VARCHAR | Change to `BOOLEAN NOT NULL` |
| Audit fields in wrong position | Move all 5 audit fields to end of column list |
| Audit field is NULLABLE | Add `NOT NULL` constraint |
| Extra audit columns (e.g., `{PREFIX}_INSERTED_BY`) | Bronze does NOT use `*_BY` columns — those belong to Silver/Gold only |
