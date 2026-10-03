# DEV Data Readiness Check

## Description
Automated data readiness check report for Gold layer schemas in the **DEV** environment. Checks row counts, null columns (100% null), SKEY validity (-1 values at 100%), last refresh timestamps, and change tracking. Generates a timestamped **HTML report** (color-coded, visually rich) saved to `DEV DataReadiness Check/` folder.

## Trigger Phrases
- "dev data readiness check"
- "run dev data readiness report"
- "dev gold layer readiness"
- "check DEV gold schemas"
- "run dev readiness report"

## Step 0 — Gather Configuration (MANDATORY, every run)

This skill has no hardcoded schema list — it asks for project-specific configuration up front:

1. **Schemas to check** — ask the user for the list of `DATABASE.SCHEMA` pairs to check (e.g. `DEV_ACME_GOLD_COMPANY_DB.COMPANY`, `DEV_ACME_GOLD_SHARED_DB.SHARED`). Accept a comma/newline-separated list.
2. **Audit-column prefix (`{PREFIX}`)** — the namespace used for audit columns on this project (e.g. `GDP`, `DW`, `EDW`; may be blank). Drives the null-check exclusion list and the "last refreshed" column below.
3. **Optional schema-config file** — ask if the user maintains a `references/schema-config.md`-style file with exception notes (tables to skip, tables scheduled for removal, SKEY→dimension mapping overrides). If they have one, read it and apply its overrides throughout this run. If not, proceed with the defaults below and skip Versioning Notes entirely.
4. **Output folder** — default to `DEV DataReadiness Check/` unless the user specifies otherwise.

Do not proceed to Step 1 until the schema list and `{PREFIX}` are known.

## Table Exclusion Rules (defaults — override via schema-config file if provided)
- Tables ending with `_HIST` — excluded
- Tables ending with `_TEMP` — excluded
- Tables ending with `_BKP` — excluded
- Tables starting with `REF_` — excluded
- Tables containing `XREF` — excluded
- `flyway_schema_history` — excluded

## Execution Steps

### COMPLETENESS RULES (CRITICAL — READ BEFORE EXECUTING)

**These rules are non-negotiable. Violating them produces an inaccurate report.**

1. **Every populated table MUST have null analysis executed** — do NOT selectively check only "key" or "large" tables. If a table has row_count > 0, it MUST be checked for 100% null columns.
2. **Every populated table MUST have SKEY checks executed** — do NOT skip any table or any SKEY column. If a table has row_count > 0 and contains `_SKEY` columns, ALL of them must be verified.
3. **Use batch queries per schema** — For each schema, first discover ALL columns and ALL SKEY columns in one query (QUERY 7 from `readiness_check_queries.sql`), then execute the null/SKEY checks for EVERY table with data. Do not cherry-pick tables.
4. **Audit columns are excluded from null checks** — Skip: `{PREFIX}_IS_ACTIVE`, `{PREFIX}_INSERTED_TS`, `{PREFIX}_INSERTED_BY`, `{PREFIX}_UPDATED_TS`, `{PREFIX}_UPDATED_BY`, and any column ending in `_HKEY` (hash keys are system-managed).
5. **Primary SKEY columns are excluded from -1 checks** — A table's own primary key SKEY (e.g., `DIM_COMPANY_SKEY` in `DIM_COMPANY`) should not be checked for -1. Only check foreign-key SKEY columns (references to other dimensions).
6. **Maintain an execution tracker** — As you execute queries, maintain a running tracker for every populated table: `{table_name: {null_checked: true/false, skey_checked: true/false, columns_discovered: N}}`. Before generating the report (Step 7), review this tracker. If ANY entry shows `false` for a populated table, you MUST execute the missing query BEFORE writing the report. This tracker is the input to Step 8's verification pass.
7. **NEVER carry forward results from a previous report** — Each run is a fresh, independent check. Do not assume null columns or SKEY issues from the previous report are still accurate. The previous report is ONLY used for change detection (Step 4 / Section 4), never as a data source for Sections 1-3. Every populated table must be re-queried from scratch every run.

### Step 1: Get Table List
For each schema, run `SHOW TABLES IN SCHEMA <schema>` and filter out excluded tables per the exclusion rules above. Include **ALL** tables that exist in the schema after applying exclusion rules — do not limit to a pre-configured list. Newly created tables are automatically picked up.

**IMPORTANT: Only include tables that physically exist in the schema.** If the user provided a schema-config file with status overrides (Not Tracking, Going to Drop, New Table) for tables that do NOT appear in `SHOW TABLES` output, omit them from the report entirely.

### Step 2: Row Counts & Refresh Timestamps
For each table:
```sql
SELECT COUNT(*) AS ROW_CNT, 
  TO_VARCHAR(MAX({PREFIX}_UPDATED_TS), 'YYYY-MM-DD HH24:MI:SS') AS LAST_REFRESHED
FROM <database>.<schema>.<table>
```

### Step 3: Null Column Analysis (100% null only)

**MANDATORY: Check EVERY table with data. No exceptions.**

For each schema, first get ALL columns for ALL non-excluded tables in one query:
```sql
SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION
FROM <database>.INFORMATION_SCHEMA.COLUMNS 
WHERE TABLE_SCHEMA = '<schema>'
  AND TABLE_NAME NOT LIKE '%_HIST' AND TABLE_NAME NOT LIKE '%_TEMP'
  AND TABLE_NAME NOT LIKE '%_BKP'
  AND TABLE_NAME NOT LIKE 'REF_%' AND TABLE_NAME NOT LIKE '%XREF%'
  AND TABLE_NAME != 'flyway_schema_history'
  AND COLUMN_NAME NOT IN ('{PREFIX}_IS_ACTIVE','{PREFIX}_INSERTED_TS','{PREFIX}_INSERTED_BY','{PREFIX}_UPDATED_TS','{PREFIX}_UPDATED_BY')
  AND COLUMN_NAME NOT LIKE '%_HKEY'
ORDER BY TABLE_NAME, ORDINAL_POSITION
```

Then for EACH table with data (row_count > 0), run a single query with ALL its columns:
```sql
SELECT COUNT(*) AS TOTAL_ROWS,
  SUM(CASE WHEN <col_1> IS NULL THEN 1 ELSE 0 END) AS <col_1>_NULL,
  SUM(CASE WHEN <col_2> IS NULL THEN 1 ELSE 0 END) AS <col_2>_NULL
  -- ... one line per non-audit, non-HKEY column
FROM <database>.<schema>.<table>
```

A column is 100% null when `<col>_NULL = TOTAL_ROWS`.

**For large tables (>1M rows)**: Use the fast existence test (QUERY 8) to save compute:
```sql
SELECT 1 FROM <database>.<schema>.<table> WHERE <column> IS NOT NULL LIMIT 1
```
If 0 rows returned → column is 100% null.

**For wide tables (>50 columns):** Split the null check into batches of up to 40 columns per query. Run multiple queries until ALL columns are covered. Example: a 167-column fact table requires ceil(167/40) = 5 queries. This is mandatory — do NOT skip wide tables.

**NO TABLE MAY BE SKIPPED.** If a table has data, it MUST have null analysis regardless of its row count or column count. Budget queries accordingly: expect ~5 queries per wide fact table. If you find yourself about to skip a table due to complexity, STOP — that is a violation of the completeness rules.

### Step 4: SKEY Validity Check (100% = -1 only)

**MANDATORY: Check EVERY SKEY column in EVERY table with data. No exceptions.**

For each schema, first discover ALL SKEY columns across all non-excluded tables:
```sql
SELECT TABLE_NAME, COLUMN_NAME 
FROM <database>.INFORMATION_SCHEMA.COLUMNS 
WHERE TABLE_SCHEMA = '<schema>' 
  AND COLUMN_NAME LIKE '%_SKEY'
  AND TABLE_NAME NOT LIKE '%_HIST' AND TABLE_NAME NOT LIKE '%_TEMP'
  AND TABLE_NAME NOT LIKE '%_BKP'
  AND TABLE_NAME NOT LIKE 'REF_%' AND TABLE_NAME NOT LIKE '%XREF%'
  AND TABLE_NAME != 'flyway_schema_history'
ORDER BY TABLE_NAME, ORDINAL_POSITION
```

Then for EACH table with data, run a single query checking ALL its SKEY columns at once:
```sql
SELECT COUNT(*) AS TOTAL_ROWS,
  SUM(CASE WHEN <skey_1> = -1 THEN 1 ELSE 0 END) AS <skey_1>_NEG1,
  SUM(CASE WHEN <skey_2> = -1 THEN 1 ELSE 0 END) AS <skey_2>_NEG1
  -- ... one line per SKEY column (excluding the table's own primary SKEY)
FROM <database>.<schema>.<table>
```

Only report if `<skey>_NEG1 = TOTAL_ROWS` (i.e., 100% = -1).

**Skip rules:**
- Skip the table's own primary SKEY (e.g., don't check `DIM_COMPANY_SKEY` in `DIM_COMPANY`)
- Skip SKEY columns whose target dimension is flagged "Going to Drop" in the user's schema-config file (if provided)

### Step 5: Capture Column Snapshot
For each table, capture the full column list from `INFORMATION_SCHEMA.COLUMNS`:
```sql
SELECT COLUMN_NAME, DATA_TYPE, ORDINAL_POSITION
FROM <database>.INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = '<schema>' AND TABLE_NAME = '<table>'
ORDER BY ORDINAL_POSITION
```
Store this snapshot in the HTML report as a hidden JSON block (see report template) for comparison in future runs.

### Step 6: Detect Changes Since Yesterday (Hybrid)

This step uses a **hybrid approach** — combining yesterday's report (for structural/baseline comparison) with live data queries (for today's activity detection).

#### 6a. Report-Based Comparison (vs Yesterday's Report)
Find the report from **yesterday's calendar date** in the output folder. Match files with yesterday's date in the filename pattern `Data_Readiness_Report_YYYY-MM-DD_*.html`. If multiple reports exist from yesterday, use the **latest** one (highest timestamp). If no report from yesterday exists, skip report-based comparisons and note "No yesterday baseline available" in Section 4.

From yesterday's report JSON snapshot, detect:
- `ROW_COUNT_CHANGE` — row count differs from yesterday (show previous → current)
- `DATA_WIPED` — table had data yesterday (row_count > 0), now empty
- `NEW_DATA` — table was empty yesterday (row_count = 0), now has data
- `SKEY_FIXED` — SKEY column was 100% = -1 yesterday, now resolved
- `SKEY_BROKEN` — SKEY column was NOT 100% = -1 yesterday, now is
- `COLUMN_ADDED` — column exists now but not in yesterday's column snapshot
- `COLUMN_REMOVED` — column existed in yesterday's snapshot but no longer exists

#### 6b. Data-Driven Detection (Live Queries)
For each populated table, run a single query to detect today's data activity:
```sql
SELECT 
  COUNT(*) AS TOTAL_ROWS,
  COUNT(CASE WHEN {PREFIX}_INSERTED_TS >= CURRENT_DATE THEN 1 END) AS ROWS_INSERTED_TODAY,
  COUNT(CASE WHEN {PREFIX}_UPDATED_TS >= CURRENT_DATE AND {PREFIX}_INSERTED_TS < CURRENT_DATE THEN 1 END) AS ROWS_UPDATED_TODAY,
  MAX({PREFIX}_UPDATED_TS) AS LAST_REFRESH
FROM <database>.<schema>.<table>
```

From this, detect:
- `NEW_ROWS` — rows inserted today (`ROWS_INSERTED_TODAY > 0`). Report the count.
- `ROWS_UPDATED` — existing rows modified today (`ROWS_UPDATED_TODAY > 0`). Report the count.
- `REFRESH_STALE` — table not refreshed since yesterday (`MAX({PREFIX}_UPDATED_TS) < CURRENT_DATE - 1`)

#### 6c. Fallback Behavior
If no yesterday report exists:
- Report-based changes (`ROW_COUNT_CHANGE`, `SKEY_FIXED`, `SKEY_BROKEN`, `COLUMN_ADDED`, `COLUMN_REMOVED`) cannot be determined — show "No yesterday baseline" note in Section 4 header
- Data-driven changes (`NEW_ROWS`, `ROWS_UPDATED`, `REFRESH_STALE`, `DATA_WIPED`) are always available regardless of whether a previous report exists

### Step 7: Generate Timestamped HTML Report
Save to: `<output folder from Step 0>/Data_Readiness_Report_YYYY-MM-DD_HHMMSS.html`

Use the template from `assets/report_template.html` for styling. The HTML report is color-coded and structured for easy visual interpretation.

### Step 8: Self-Verification & Report Correction

**This step is MANDATORY. The report is not final until verification passes.**

After generating the report in Step 7, perform a full verification pass to ensure accuracy and completeness:

#### 8a. Build Verification Checklist
Using the master table list from Step 1 and row counts from Step 2, build a checklist of ALL populated tables (row_count > 0). For each, confirm:
- Was a null-analysis query executed? (every non-audit, non-HKEY column checked)
- Was an SKEY-validity query executed? (every FK SKEY column checked)
- Was column count captured from INFORMATION_SCHEMA?

#### 8b. Cross-Reference Report Against Checklist
Parse the generated HTML report and verify:
1. **Table coverage** — Every populated table from Step 1 appears in Section 1 with status "Has Data" and has non-empty null/SKEY results (even if both are clean).
2. **Null completeness** — For each populated table, confirm that the number of columns checked matches the expected column count (minus audit columns and HKEY columns). If a wide table required N batches, confirm all N were executed.
3. **SKEY completeness** — For each populated table with FK SKEY columns, confirm an SKEY query was executed and results are recorded.
4. **Change detection completeness** — Verify:
   - Yesterday's report was located (or noted as unavailable)
   - Data-driven queries (`{PREFIX}_INSERTED_TS`/`{PREFIX}_UPDATED_TS`) were executed for ALL populated tables
   - If yesterday's report exists: column snapshot comparison was performed for COLUMN_ADDED/COLUMN_REMOVED
5. **Summary card accuracy** — Verify the summary card numbers:
   - "Tables with Data" = count of tables with row_count > 0 (excluding any config-file exclusions)
   - "Tables Empty" = count of tables with row_count = 0 (excluding any config-file exclusions)
   - "SKEY Issues (100%)" = count of rows in Section 3
   - "Changes Detected" = count of rows in Section 4

#### 8c. Execute Missing Checks
If ANY gaps are found in 8b:
- Run the missing null-analysis queries for any skipped tables
- Run the missing SKEY-validity queries for any skipped tables
- Run missing data-driven change detection queries (`{PREFIX}_INSERTED_TS`/`{PREFIX}_UPDATED_TS`) for any skipped tables
- Add any missing COLUMN_ADDED / COLUMN_REMOVED entries to Section 4 (if yesterday's report exists)
- Correct any inaccurate summary card numbers
- Add any missing SKEY_FIXED / SKEY_BROKEN entries (compare current SKEY issues against yesterday's report `skey_issues` JSON)

#### 8d. Rewrite the Final Report
If corrections were made in 8c, **overwrite** the same timestamped HTML file with the corrected version. The final report footer MUST include a verification badge:

```
Verification: PASSED — All [N] populated tables checked. Null analysis: [N]/[N]. SKEY analysis: [N]/[N].
```

Where:
- First `[N]` = total number of populated tables
- `Null analysis: [N]/[N]` = tables with null check completed / total populated tables
- `SKEY analysis: [N]/[N]` = tables with SKEY check completed / total populated tables with FK SKEY columns

If verification finds zero gaps, the report from Step 7 is final — just confirm "Verification: PASSED" in output. Do NOT skip this step or assume the report is correct without explicit verification.

## Report HTML Structure

The HTML report contains 4 color-coded sections:

### Section 1: MAIN REPORT (grouped by schema)
Tables are **grouped by schema** with a schema header row spanning all columns. Each schema section shows all its tables underneath. The schema header row uses a dark background to visually separate groups.

Structure per schema group:
```
┌─────────────────────────────────────────────────────────────┐
│ <DATABASE>.<SCHEMA>                                          │  ← schema header (colspan)
├──────┬──────────┬─────────┬────────┬───────┬──────┬────────┤
│Table │ Row Count│ Status  │Null Col│SKEY-1 │Cols  │Refresh │
├──────┼──────────┼─────────┼────────┼───────┼──────┼────────┤
│ ...  │  ...     │  ...    │  ...   │  ...  │  ... │  ...   │
└──────┴──────────┴─────────┴────────┴───────┴──────┴────────┘
```

Columns: Table, Row Count, Status, Null Columns (100%), SKEY -1 (100%), Column Count, Last Refreshed

**Row color coding:**
- Green (#d4edda) — Has Data, no issues
- Yellow (#fff3cd) — Has Data with issues (null columns or SKEY problems)
- Red (#f8d7da) — No Data (empty or regression)
- Grey (#e2e3e5) — Not Tracking / Going to Drop (only if user supplied a schema-config file)
- Blue (#cce5ff) — New Table (planned, only if user supplied a schema-config file)

**Status values:**
- `Has Data` — table has rows
- `No Data` — table is empty (was never populated or regression)
- `Not Tracking` — excluded per the user's schema-config file (if provided)
- `Going to Drop` — scheduled for removal per the user's schema-config file (if provided)
- `New Table` — planned but not yet created per the user's schema-config file (if provided)

### Section 2: TABLES WITH NO DATA (sub-table)
Columns: Schema, Table, Previous Row Count, Notes
Red-highlighted rows for regressions.

### Section 3: SKEY ISSUES — 100% = -1 only (sub-table)
Columns: Schema, Table, SKEY Column, Rows = -1, Total Rows, Target Dimension
Yellow-highlighted rows.

### Section 4: CHANGES SINCE YESTERDAY (sub-table)
Columns: Schema, Table, Change Type, Previous Value, Current Value, Notes

This section uses a **hybrid approach**: report-based comparison (vs yesterday's report) + data-driven detection (live timestamp queries).

**Report-based change types (compared to yesterday's report):**
- `ROW_COUNT_CHANGE` — row count differs from yesterday's report (yellow)
- `DATA_WIPED` — had data yesterday, now empty (red)
- `NEW_DATA` — was empty yesterday, now has data (green)
- `SKEY_FIXED` — SKEY was 100% = -1 yesterday, now resolved (green)
- `SKEY_BROKEN` — SKEY was not 100% = -1 yesterday, now is (red)
- `COLUMN_ADDED` — new column added since yesterday's column snapshot (blue)
- `COLUMN_REMOVED` — column removed since yesterday's column snapshot (red)

**Data-driven change types (live query using `{PREFIX}_INSERTED_TS` / `{PREFIX}_UPDATED_TS`):**
- `NEW_ROWS` — rows inserted today (`{PREFIX}_INSERTED_TS >= CURRENT_DATE`) (green)
- `ROWS_UPDATED` — existing rows modified today (`{PREFIX}_UPDATED_TS >= CURRENT_DATE`, `{PREFIX}_INSERTED_TS < CURRENT_DATE`) (yellow)
- `REFRESH_STALE` — table not refreshed since yesterday (`MAX({PREFIX}_UPDATED_TS) < CURRENT_DATE - 1`) (yellow)

**Note:** If no report from yesterday exists, report-based change types are unavailable. Section 4 header will note "No yesterday baseline available — showing data-driven changes only."

## Notes
- If provided, read the user's `schema-config.md`-style file for versioning notes (Going to Drop, Not Tracking, New Table) — see [./references/schema-config.md](./references/schema-config.md) for the expected format (ships as an empty template).
- Read `assets/readiness_check_queries.sql` for optimized SQL query templates.
- Read `assets/report_template.html` for the HTML/CSS template structure.
- Output folder: as gathered in Step 0 (default `DEV DataReadiness Check/`).
- The report is cumulative — each run captures a snapshot; previous reports are preserved for history.
- **Report footer MUST include verification status**: `Verification: PASSED — All [N] populated tables checked. Null analysis: [N]/[N]. SKEY analysis: [N]/[N].` This confirms the self-verification step (Step 8) completed successfully.
- **No report is considered final until Step 8 verification passes.** If the executor outputs a report without the verification badge, the report is incomplete and must be re-run.
