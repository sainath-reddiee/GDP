# Bronze — Iceberg Table Conventions

`{PREFIX}` is the project's audit-column / shared-object namespace (e.g. `GDP`, `DW`, `EDW`; may be blank — established once per project, see SKILL.md).

## Mandatory Iceberg Format

All Bronze tables MUST be Iceberg tables. Standard (non-Iceberg) tables are a compliance violation.

---

## Iceberg Footer Requirements

Every Bronze Iceberg table must include these properties:

```sql
EXTERNAL_VOLUME = '<volume_name>'
CATALOG = 'SNOWFLAKE'
BASE_LOCATION = '<path>/'
```

### EXTERNAL_VOLUME naming convention

| Environment | Volume Name |
|---|---|
| DEV | `{PREFIX}_SF_DEV_BRONZE` |
| QA | `{PREFIX}_SF_QA_BRONZE` |
| UAT | `{PREFIX}_SF_UAT_BRONZE` |
| PROD | `{PREFIX}_SF_PROD_BRONZE` |

### BASE_LOCATION convention

```
{prefix_lower}-sf-{env}-bronze/{schema_lower}/{TABLE_NAME}/
```

Examples (using `{PREFIX}` = `GDP`):
- `gdp-sf-dev-bronze/bronze_dnb/COMPANY_PROFILE/`
- `gdp-sf-dev-bronze/bronze_lightbox/BUILDINGS/`

---

## DDL Structure

```sql
CREATE OR REPLACE ICEBERG TABLE {DATABASE}.{SCHEMA}.{TABLE_NAME} (
    -- Source columns (preserve source order and types)
    {column_1}    {type}    {nullable}    COMMENT '{description}',
    {column_2}    {type}    {nullable}    COMMENT '{description}',
    ...

    -- Audit columns (always last, always NOT NULL)
    {PREFIX}_INSERTED_TS    TIMESTAMP_NTZ(6)    NOT NULL    COMMENT 'Timestamp when record was inserted into Bronze',
    {PREFIX}_IS_ACTIVE      BOOLEAN             NOT NULL    COMMENT 'Soft delete flag — TRUE indicates active record',
    {PREFIX}_ROW_HASH       STRING              NOT NULL    COMMENT 'Hash of business columns for change detection',
    {PREFIX}_UPDATED_TS     TIMESTAMP_NTZ(6)    NOT NULL    COMMENT 'Timestamp when record was last updated in Bronze',
    ETL_CREATED_TS          TIMESTAMP_NTZ(6)    NOT NULL    COMMENT 'ETL pipeline creation timestamp from source staging'
)
EXTERNAL_VOLUME = '{EXTERNAL_VOLUME}'
CATALOG = 'SNOWFLAKE'
BASE_LOCATION = '{BASE_LOCATION}';
```

---

## Key Differences from Silver/Gold Iceberg Tables

| Aspect | Bronze | Silver/Gold |
|---|---|---|
| Audit columns | 5 (`{PREFIX}_INSERTED_TS`, `{PREFIX}_IS_ACTIVE`, `{PREFIX}_ROW_HASH`, `{PREFIX}_UPDATED_TS`, `ETL_CREATED_TS`) | 5 (`{PREFIX}_IS_ACTIVE`, `{PREFIX}_INSERTED_TS`, `{PREFIX}_INSERTED_BY`, `{PREFIX}_UPDATED_TS`, `{PREFIX}_UPDATED_BY`) |
| Hash column | `{PREFIX}_ROW_HASH` | `{TABLE}_HKEY` |
| `*_BY` columns | Not used | Required (`{PREFIX}_INSERTED_BY`, `{PREFIX}_UPDATED_BY`) |
| `ETL_CREATED_TS` | Required | Not used |
| Column types | Preserve source types exactly | Normalize to standard types |
| Column order | Source order + audit last | Zone-based ordering (keys → business → hash → audit) |
| Surrogate keys (_SKEY) | Not required | Required |
| SOURCE_UNIQUE_ID | Not required | Required |
| PK constraints | Optional | Required |

---

## Non-Iceberg Table Handling

If a table in a Bronze schema is NOT Iceberg:
1. Flag it as a **compliance violation** in the report
2. Note whether it is:
   - A staging/temp table (acceptable if documented)
   - A permanent table that should be Iceberg (must fix)
3. When generating deployment scripts, emit the corrected Iceberg version with a `-- REVIEW: converted from non-Iceberg` comment

---

## _HIST Table Exclusion

Tables ending with `_HIST` are:
- **Noted** in the schema inventory
- **Excluded** from the deployment `.sql` file
- Managed separately by the ETL pipeline team
- Never included in consolidated deployment scripts
