# Bronze — Deployment Formatting Standards

`{PREFIX}` is the project's audit-column / shared-object namespace (e.g. `GDP`, `DW`, `EDW`; may be blank — established once per project, see SKILL.md).

## Enterprise Deployment Rules

### 1. Dynamic Environment Placeholders

ALL environment-specific references use `${ENV_NAME}`:

| Element | Format | Example (DEV, `{PREFIX}` = `GDP`) |
|---|---|---|
| Database | `${ENV_NAME}_{PREFIX}_BRONZE_DB` | `DEV_GDP_BRONZE_DB` |
| External volume | `{PREFIX}_SF_${ENV_NAME}_BRONZE` | `GDP_SF_DEV_BRONZE` |
| Base location | `{prefix_lower}-sf-${ENV_NAME}-bronze/{SCHEMA}/{TABLE}/` | `gdp-sf-dev-bronze/BRONZE_BOE/MY_TABLE/` |

**NEVER hardcode** DEV, QA, UAT, or PROD in the output.

---

### 2. CREATE IF NOT EXISTS (not CREATE OR REPLACE)

```sql
CREATE ICEBERG TABLE IF NOT EXISTS ${ENV_NAME}_{PREFIX}_BRONZE_DB.{SCHEMA}.{TABLE_NAME} (
```

**FORBIDDEN:**
- `CREATE OR REPLACE` — destructive, not deployment-safe
- Bare `CREATE TABLE` without `IF NOT EXISTS`

---

### 3. Excluded Statements

The following must NEVER appear in deployment scripts:

| Forbidden | Reason |
|---|---|
| `USE WAREHOUSE ...` | Warehouse is set by deployment pipeline |
| `CREATE SCHEMA ...` | Schema creation managed separately |
| `CREATE OR REPLACE ...` | Destructive — use IF NOT EXISTS |

---

### 4. Comment Rules — METADATA-ONLY (NO AI GENERATION)

**Comments must come ONLY from actual Snowflake metadata** retrieved via:
- `GET_DDL()` output
- `INFORMATION_SCHEMA.COLUMNS.COMMENT`
- `DESCRIBE TABLE` comment field

**Rules:**
1. Column has a COMMENT in Snowflake metadata → include it verbatim: `COMMENT '...'`
2. Column has NO COMMENT in Snowflake metadata (NULL or empty) → emit column WITHOUT a COMMENT clause
3. Table has a COMMENT → include as `COMMENT = '...'` after closing parenthesis
4. Table has NO COMMENT → omit COMMENT clause entirely

**NEVER:**
- Infer or generate column descriptions from column names
- Profile column values to write business meaning
- Generate semantic summaries or narrative explanations
- Add comments like "This column likely represents...", "A unique identifier for...", "Contains values such as..."
- Enrich, rephrase, or edit existing source comments
- Make judgments about whether a source comment is "AI-generated" — preserve ALL source metadata comments verbatim regardless of their style
- Skip or omit comments for columns in wide tables (100+ columns) — ALL columns must be processed

**Large/wide table handling:**
- For tables with many columns (e.g., a 360-column source table), `GET_DDL()` output is often truncated and drops COMMENT clauses
- **ALWAYS query `INFORMATION_SCHEMA.COLUMNS` with the `COMMENT` field** as the authoritative source for column comments on large tables
- Process every single column — never stop at a certain count or batch
- Never summarize groups of similar columns (e.g., "240 additional DECIMAL columns") — emit each individually with its comment if one exists

**The DDL must reflect source Snowflake metadata exactly — nothing added, nothing removed, nothing skipped.**

---

### 5. Empty Section Handling

**COMPLETELY OMIT** sections for object types that don't exist.

**NEVER generate:**
```sql
-- ============================================================================
-- VIEWS
-- ============================================================================
-- (none)
```

Only emit sections that contain actual objects.

---

### 6. Header Format

```sql
-- ============================================================================
-- ${ENV_NAME}_{PREFIX}_BRONZE_DB.{SCHEMA} - Complete Object DDLs
-- ============================================================================
```

No additional metadata lines (no date, no summary counts, no rules block).

---

### 7. Table Section Format

```sql
-- ----------------------------------------------------------------------------
-- TABLE: {TABLE_NAME}
-- ----------------------------------------------------------------------------
CREATE ICEBERG TABLE IF NOT EXISTS ${ENV_NAME}_{PREFIX}_BRONZE_DB.{SCHEMA}.{TABLE_NAME} (
    "Column1"    STRING,
    "Column2"    TIMESTAMP_NTZ(6),
    ...
    {PREFIX}_INSERTED_TS    TIMESTAMP_NTZ(6),
    {PREFIX}_IS_ACTIVE      BOOLEAN,
    {PREFIX}_ROW_HASH       BINARY,
    {PREFIX}_UPDATED_TS     TIMESTAMP_NTZ(6),
    ETL_CREATED_TS          TIMESTAMP_NTZ(6)
)
    EXTERNAL_VOLUME = '{PREFIX}_SF_${ENV_NAME}_BRONZE'
    CATALOG = 'SNOWFLAKE'
    BASE_LOCATION = '{prefix_lower}-sf-${ENV_NAME}-bronze/{SCHEMA}/{TABLE_NAME}/';
```

**CRITICAL — Faithful extraction**: Audit columns must be emitted with the EXACT nullability and type from the source table. Do NOT add NOT NULL if the source column is nullable. Do NOT change BINARY to STRING. Report discrepancies in the compliance report only.

---

### 8. Iceberg Footer (EXACT format)

```sql
    EXTERNAL_VOLUME = '{PREFIX}_SF_${ENV_NAME}_BRONZE'
    CATALOG = 'SNOWFLAKE'
    BASE_LOCATION = '{prefix_lower}-sf-${ENV_NAME}-bronze/{SCHEMA}/{TABLE_NAME}/';
```

- Indented with 4 spaces
- Single quotes around values
- Semicolon at end of BASE_LOCATION line
- No `ICEBERG_VERSION` line (Snowflake defaults to latest)

---

### 9. Deployment Readiness Checklist

Before emitting the final file, verify:

- [ ] No hardcoded DEV/QA/UAT/PROD anywhere
- [ ] No `USE WAREHOUSE`
- [ ] No `CREATE SCHEMA`
- [ ] No `CREATE OR REPLACE`
- [ ] No verbose narrative comments
- [ ] No empty sections
- [ ] All tables use `CREATE ICEBERG TABLE IF NOT EXISTS`
- [ ] All databases reference `${ENV_NAME}_{PREFIX}_BRONZE_DB`
- [ ] All external volumes reference `{PREFIX}_SF_${ENV_NAME}_BRONZE`
- [ ] All base locations reference `{prefix_lower}-sf-${ENV_NAME}-bronze/`
- [ ] _HIST tables excluded
- [ ] File works for any environment by setting `${ENV_NAME}`
